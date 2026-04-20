"""
Agent 主循环模块。

架构角色：
    AgentLoop 是整个 agent 的大脑。它把所有其他组件串联起来，
    实现"用户输入 → LLM → 工具调用 → 结果回传 → 最终回复"的完整流程。

    数据流位置（Stage 0 版）：
        AgentLoop.run(user_input)
          ├─ MessageHistory.append_user()        # 记录用户消息
          ├─ loop:
          │   ├─ AnthropicClient.send()          # 调用 LLM
          │   ├─ MessageHistory.append_assistant() # 记录 LLM 响应
          │   ├─ 若有 tool_use block：
          │   │   ├─ ToolRegistry.dispatch()      # 执行工具（成功）
          │   │   └─ MessageHistory.append_tool_result()  # 记录结果
          │   └─ 若无 tool_use → 返回文本
          └─ 返回最终文本

学习点（主循环设计）：
    - while True + 条件 return：LLM 可能需要多轮工具调用才能给出最终答案，
      所以用无限循环，直到 LLM 返回纯文本（无 tool_use）时才退出。
    - 工具错误不 crash agent：try/except 捕获所有工具异常，包装成 is_error=True 的
      tool_result 返回给 LLM，让 LLM 自行决定是否重试或给出错误说明。
    - 消息历史的"滚雪球"：每轮都把完整历史发给无状态的 API，
      所以历史越长，每轮的 token 消耗越大。这就是 Stage 4 要做压缩的原因。

Stage 0 的限制（后续阶段完善）：
    - 无 max_turns 保护（Stage 1）：理论上可能无限循环。
    - 工具结果无截断（Stage 1）：大文件可能耗尽 context window。
    - 无权限层（Stage 2）：工具调用无需确认。
    - 非流式（Stage 3）：用户需等全部完成才看到输出。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from my_agent.core.client import AnthropicClient
from my_agent.core.history import MessageHistory
from my_agent.tools.registry import ToolRegistry

# TYPE_CHECKING guard：只在 mypy 分析时导入 anthropic 类型。
# 运行时不需要，因为 content blocks 通过鸭子类型访问（block.type / block.text 等）。
if TYPE_CHECKING:
    from anthropic.types import ContentBlock


def _extract_text(content: list[ContentBlock]) -> str:
    """从 assistant 消息的 content blocks 列表中提取所有文本，拼接返回。

    当 LLM 返回纯文本（stop_reason == "end_turn"，无 tool_use block）时调用。
    通常只有一个 TextBlock，但规范上 content 是列表，所以这里做了通用处理。

    Args:
        content: assistant 消息的 content block 列表，此时只含 TextBlock。

    Returns:
        所有 TextBlock 的 .text 拼接后的字符串。若无 TextBlock，返回空字符串。
    """
    # 鸭子类型：不用 isinstance(block, TextBlock)，只检查 block.type == "text"
    return "".join(block.text for block in content if block.type == "text")


class AgentLoop:
    """Agent 的核心主循环，驱动 LLM 与工具之间的多轮交互。

    设计为无状态类：每次调用 run() 都创建一个新的 MessageHistory，
    所以同一个 AgentLoop 实例可以处理多个独立的会话（虽然 Stage 0 里 CLI 只跑一次）。

    线程安全：run() 是 async 函数，在单线程事件循环内运行，无并发问题。
    """

    def __init__(
        self,
        client: AnthropicClient,
        registry: ToolRegistry,
        logger: logging.Logger,
    ) -> None:
        """
        Args:
            client:   AnthropicClient 实例，负责实际的 API 调用。
                      测试时可以传入 FakeLLM（鸭子类型，无需继承）。
            registry: ToolRegistry 实例，包含所有已注册的工具。
            logger:   Python 标准库 Logger 实例，用于调试日志。
                      Stage 0 使用 logging，Stage 3+ 会迁移到 structlog。
        """
        self._client = client
        self._registry = registry
        self._logger = logger

    async def run(self, user_input: str) -> str:
        """执行一次完整的 agent 交互，返回最终的文本回复。

        主循环协议（Anthropic tool_use 双向协议）：
            1. user 消息 → LLM
            2. LLM 返回 tool_use block → 我们执行工具 → tool_result → 回到步骤 1
            3. LLM 返回纯文本（end_turn）→ 提取文本并返回

        Args:
            user_input: 用户输入的原始文本。

        Returns:
            LLM 最终的文本回复（所有工具调用完成后）。
        """
        # 每次 run() 创建独立的历史，不同调用之间互不影响
        history = MessageHistory()
        history.append_user(user_input)

        self._logger.debug("AgentLoop started", extra={"user_input": user_input})

        while True:
            # ── 第一步：把完整历史发给 LLM ────────────────────────────────
            # Anthropic API 是无状态的：每次都要发完整历史（"无状态" = "每次从零开始"）
            response = await self._client.send(
                history.to_anthropic(),
                self._registry.to_anthropic_schemas(),
            )
            self._logger.debug(
                "LLM response received",
                extra={"stop_reason": getattr(response, "stop_reason", "unknown")},
            )

            # ── 第二步：把 LLM 响应记入历史 ───────────────────────────────
            # 重要：必须先 append_assistant，再 append_tool_result。
            # Anthropic 协议要求 tool_result 紧跟在对应的 assistant 消息之后，
            # 且 tool_use_id 必须与 assistant 消息里的 tool_use block id 完全匹配。
            history.append_assistant(response.content)

            # ── 第三步：检查是否有工具调用 ────────────────────────────────
            # 从 content blocks 里过滤出所有 tool_use block
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            if not tool_uses:
                # LLM 直接给出文本回答（无工具调用）→ 提取并返回
                final_text = _extract_text(response.content)
                self._logger.debug("AgentLoop finished", extra={"text_len": len(final_text)})
                return final_text

            # ── 第四步：执行所有工具并记录结果 ───────────────────────────
            # 注意：一轮 LLM 响应可能包含多个 tool_use block（并行工具调用）。
            # 所有 tool_result 必须放进同一条 user 消息（MessageHistory 的 append_tool_result 会自动合并）。
            for tu in tool_uses:
                try:
                    result = await self._registry.dispatch(tu.name, tu.input)
                    history.append_tool_result(tu.id, result)
                    self._logger.debug(
                        "Tool executed",
                        extra={"tool": tu.name, "result_len": len(result)},
                    )
                except Exception as e:
                    # 工具失败不 crash agent：
                    # 把错误信息作为 is_error=True 的 tool_result 发回给 LLM，
                    # LLM 会看到错误并决定下一步（重试？报告给用户？）
                    error_msg = f"{type(e).__name__}: {e}"
                    history.append_tool_result(tu.id, error_msg, is_error=True)
                    self._logger.debug(
                        "Tool error",
                        extra={"tool": tu.name, "error": error_msg},
                    )

            # 回到循环顶部，把包含 tool_result 的新历史再次发给 LLM
