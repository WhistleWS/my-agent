"""
pytest 全局 fixture 和测试辅助类。

FakeLLM 设计目标：
    替代真实的 AnthropicClient，让单元测试无需调用任何 API，
    通过"脚本化对话"（scripted_turns）预录 LLM 的响应序列，
    每次 send() 返回下一条预录响应。

    优点：
    - 零 token、无网络请求、速度快（CI 友好）
    - 可精确控制 LLM 行为（测什么场景就录什么脚本）
    - 不依赖 anthropic SDK 的具体返回格式（用 SimpleNamespace 鸭子模拟）

辅助函数：
    fake_text_block()     → 模拟 TextBlock（.type="text", .text=...）
    fake_tool_use_block() → 模拟 ToolUseBlock（.type="tool_use", .id=..., .name=..., .input=...）
    fake_message()        → 模拟 anthropic.types.Message（.content=..., .usage=...）

学习点：
    - SimpleNamespace：标准库里最轻量的"假对象"，可以动态设置任意属性。
      等价于 MagicMock，但更简洁、无魔法方法。
    - yield fixture：conftest 里的 fixture 函数可以 yield 一个对象，
      yield 后的代码在测试结束后执行（相当于 teardown）。
      本文件的 fixture 是简单工厂，不需要清理，直接 return 即可。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from my_agent.core.client import TokenUsage


# ---------------------------------------------------------------------------
# 假消息构造辅助函数
# ---------------------------------------------------------------------------


def fake_text_block(text: str) -> SimpleNamespace:
    """创建模拟 TextBlock，只需 .type 和 .text 属性。

    用于构建 fake_message 的 content 列表。
    AgentLoop 和 MessageHistory 通过 block.type == "text" 做分支（鸭子类型），
    所以只要有这两个属性就够了。
    """
    return SimpleNamespace(type="text", text=text)


def fake_tool_use_block(
    id: str, name: str, input: dict[str, Any]  # noqa: A002
) -> SimpleNamespace:
    """创建模拟 ToolUseBlock（.type="tool_use", .id, .name, .input）。

    Args:
        id:    工具调用的唯一标识符，tool_result 里必须用相同的 id 回传。
        name:  工具名，ToolRegistry 按这个名字找工具。
        input: 工具调用参数 dict，dispatch() 会把它传给 Pydantic model_validate()。
    """
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def fake_message(
    content: list[SimpleNamespace],
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> SimpleNamespace:
    """创建模拟 anthropic.types.Message。

    AgentLoop 访问的字段：
        .content  → content block 列表
        .usage.input_tokens / .usage.output_tokens → token 计数

    SimpleNamespace 嵌套学习点：
        msg.usage = SimpleNamespace(input_tokens=10, ...) 创建嵌套属性。
        AnthropicClient.TokenUsage.add(msg) 会读 msg.usage.input_tokens，
        FakeLLM 继承 TokenUsage 的 add()，所以假消息也要有这个嵌套结构。
    """
    msg = SimpleNamespace()
    msg.content = content
    msg.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    return msg


# ---------------------------------------------------------------------------
# FakeLLM — AnthropicClient 的测试替身
# ---------------------------------------------------------------------------


class FakeLLM:
    """预录响应的 LLM 客户端，用于单元测试中替代 AnthropicClient。

    使用方式：
        turns = [
            fake_message([fake_tool_use_block("tu_1", "read_file", {"path": "a.txt"})]),
            fake_message([fake_text_block("文件内容是 hello")]),
        ]
        llm = FakeLLM(scripted_turns=turns)
        loop = AgentLoop(client=llm, ...)  # 鸭子类型替换

    每次调用 send() 返回 scripted_turns 的下一项。
    如果调用次数超过预录数量，抛出 StopIteration（测试 bug，不是代码 bug）。

    鸭子类型学习点：
        FakeLLM 没有继承 AnthropicClient，但实现了相同的接口（send + usage）。
        Python 不要求显式声明"实现了某接口"（不像 Java 的 implements）。
        只要对象有对应的属性和方法，就可以在运行时互换（鸭子类型）。
        mypy --strict 在 src/ 下强制检查，但 tests/ 不在检查范围内，
        所以 FakeLLM 传给 AgentLoop 时不会报类型错误。
    """

    def __init__(self, scripted_turns: list[SimpleNamespace]) -> None:
        self._turns = scripted_turns
        self._call_count = 0
        self._usage = TokenUsage()

    async def send(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> SimpleNamespace:
        """返回下一条预录响应，同时累计 token 用量。

        Args:
            messages: 被忽略（测试里只关心响应，不检查入参）。
            tools:    被忽略（同上）。

        Returns:
            scripted_turns[call_count] 的假消息对象。

        Raises:
            IndexError: 调用次数超过预录数量，说明测试脚本写错了。
        """
        if self._call_count >= len(self._turns):
            raise IndexError(
                f"FakeLLM: send() 被调用了 {self._call_count + 1} 次，"
                f"但只预录了 {len(self._turns)} 条响应。"
            )
        msg = self._turns[self._call_count]
        self._call_count += 1
        # 累计 token，保持和 AnthropicClient 相同的行为
        self._usage.add(msg)
        return msg

    @property
    def usage(self) -> TokenUsage:
        return self._usage

    @property
    def call_count(self) -> int:
        """已调用的次数，便于在测试里断言 LLM 被调用了几轮。"""
        return self._call_count
