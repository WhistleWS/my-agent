"""
对话消息历史管理模块。

架构角色：
    MessageHistory 是 agent 主循环的"记忆"。每轮调用 LLM 时，
    AgentLoop 把 history.to_anthropic() 作为 messages 参数整体发过去。
    Anthropic API 是无状态的——服务端不保存历史，所以每次都要把完整历史发过去。

    数据流位置：
        AgentLoop
          ├─ history.append_user(user_input)      # 开始
          ├─ history.append_assistant(response)   # 每轮 LLM 调用后
          ├─ history.append_tool_result(...)      # 每个工具执行后
          └─ history.to_anthropic()               # 作为下一轮 LLM 调用的 messages 参数

Anthropic 消息格式（必读协议）：
    role 必须严格交替，不能连续两条 user 或两条 assistant：
        user     → {"role": "user", "content": "文本"} 或工具结果列表
        assistant → {"role": "assistant", "content": [TextBlock | ToolUseBlock, ...]}

    tool_use 协议的关键规则：
        1. assistant 返回 tool_use block → 我们必须在下一条 user 消息里提供对应的 tool_result
        2. tool_result 里的 tool_use_id 必须与 assistant 消息里 tool_use block 的 id 完全匹配
        3. 如果一轮 assistant 调用了多个工具，所有 tool_result 要放在同一条 user 消息里

学习点：
    - TYPE_CHECKING guard：只在 mypy 分析时导入 anthropic 类型，运行时不引入依赖，
      遵守"只有 core/client.py 可以 import anthropic"的全局约束
    - 鸭子类型（Duck Typing）：用 block.type == "text" 而不是 isinstance(block, TextBlock)，
      运行时无需导入具体类型，更 Pythonic
    - list[dict[str, Any]]：灵活的内部存储格式，直接对应 Anthropic API 的 JSON 结构
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# TYPE_CHECKING 是一个特殊常量：
#   - mypy 分析时：值为 True → 执行 import，获得类型信息
#   - 运行时：值为 False → 跳过 import，不引入 anthropic 依赖
# 配合 from __future__ import annotations，函数签名里的类型注解会被当作字符串处理，
# 所以运行时不会因为 ContentBlock 未定义而报 NameError。
if TYPE_CHECKING:
    from anthropic.types import ContentBlock


class MessageHistory:
    """维护完整对话历史，格式严格遵守 Anthropic messages API 规范。

    设计决策：
        内部用 list[dict[str, Any]] 直接存储 Anthropic API 格式的消息，
        避免额外的序列化层。to_anthropic() 返回这个列表的浅拷贝即可直接传给 SDK。

    线程安全：不是线程安全的。每个 AgentLoop 实例独享一个 MessageHistory。
    """

    def __init__(self) -> None:
        # 内部消息列表，每项格式：{"role": "user"|"assistant", "content": ...}
        self._messages: list[dict[str, Any]] = []

    def append_user(self, content: str) -> None:
        """追加一条普通文本用户消息（对话开头或非工具相关的用户输入）。

        Anthropic 格式：{"role": "user", "content": "文本字符串"}

        Args:
            content: 用户输入的纯文本。
        """
        self._messages.append({"role": "user", "content": content})

    def append_assistant(self, blocks: list[ContentBlock]) -> None:
        """追加一条 assistant 消息，把 SDK 返回的 content blocks 转成 dict 格式。

        Anthropic assistant 消息格式：
            {"role": "assistant", "content": [
                {"type": "text", "text": "..."},
                {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
            ]}

        鸭子类型说明：
            本方法用 block.type == "text" 做分支，而不是 isinstance(block, TextBlock)。
            原因：运行时不能 import anthropic（全局约束），但可以检查属性值。
            鸭子类型的精髓：不问"你是什么"，只问"你能做什么（有什么属性）"。

        Args:
            blocks: Anthropic Message.content，SDK 返回的内容块列表。
                    可以是 TextBlock、ToolUseBlock 的混合。
        """
        content: list[dict[str, Any]] = []
        for block in blocks:
            if block.type == "text":
                # TextBlock：有 .text 属性，存纯文本
                # mypy 通过 Literal["text"] 判别式把 block 收窄为 TextBlock，所以 .text 合法
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                # ToolUseBlock：有 .id .name .input 属性
                # .input 是 dict，存模型生成的工具调用参数
                content.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
            # 其他 block 类型（如 thinking block）Stage 0 暂不处理，直接跳过
        self._messages.append({"role": "assistant", "content": content})

    def append_tool_result(self, tool_use_id: str, content: str, is_error: bool = False) -> None:
        """追加一条工具执行结果。

        Anthropic 协议关键规则（工具结果必须分组）：
            若 assistant 一轮调了 N 个工具，需把 N 个 tool_result 放进同一条 user 消息。
            本方法通过检查最后一条消息是否已经是 tool_result 批次来决定追加还是新建。

        Anthropic 工具结果消息格式：
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_001", "content": "结果文本"},
                {"type": "tool_result", "tool_use_id": "tu_002", "content": "错误信息", "is_error": true},
            ]}

        Args:
            tool_use_id: 对应 assistant 消息里 tool_use block 的 id，必须精确匹配。
            content:     工具执行输出（成功时是结果，失败时是错误描述）。
            is_error:    True 时 API 会告知模型这次工具执行失败，模型可以自行决定是否重试。
        """
        result_block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            # 只在需要时加 is_error 字段，避免发送冗余的 false 给 API
            result_block["is_error"] = True

        # 判断是否要追加到上一条 user 消息（同批次多工具结果）
        last = self._messages[-1] if self._messages else None
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]  # 非空列表
            and isinstance(last["content"][0], dict)
            and last["content"][0].get("type") == "tool_result"
        ):
            # 同一批次的工具结果：追加到已有的 user 消息里
            last["content"].append(result_block)
        else:
            # 新建一条 user 消息，内容是工具结果列表（注意：content 是 list，不是 str）
            self._messages.append({"role": "user", "content": [result_block]})

    def to_anthropic(self) -> list[dict[str, Any]]:
        """返回完整消息历史，可直接传给 Anthropic messages.create(messages=...)。

        Returns:
            内部列表的浅拷贝。调用方修改返回的列表不会影响 MessageHistory 内部状态，
            但修改列表内部的 dict 元素仍然会影响（如需完全隔离，用 copy.deepcopy）。
        """
        # list(self._messages) 创建新列表，但元素（dict）仍是同一对象引用
        return list(self._messages)

    def __len__(self) -> int:
        """返回当前消息总条数，便于测试断言和调试日志。"""
        return len(self._messages)
