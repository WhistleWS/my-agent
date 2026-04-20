"""
工具注册表模块。

架构角色：
    ToolRegistry 是工具的"黄页"。AgentLoop 在发起 LLM 调用时通过
    to_anthropic_schemas() 把所有工具的 schema 一次性传给 API；
    收到 tool_use block 后，通过 dispatch() 按名字找到对应工具并执行。

    数据流位置：
        工具实例 → register() → ToolRegistry
        ToolRegistry.to_anthropic_schemas() → AnthropicClient.send(tools=...)
        LLM 返回 tool_use block → AgentLoop → dispatch(name, raw_input) → Tool.execute()

设计决策：
    - 内部用 dict[str, Tool] 按工具名索引，O(1) 查找。
    - dispatch() 不捕获 KeyError 和 ValidationError，让 AgentLoop 决定如何处理
      （Stage 0 里 loop 会把错误包装成 is_error=True 的 tool_result 发回给 LLM）。
    - raw_input 是 LLM 生成的 dict，需经 Pydantic model_validate() 校验后再传给 execute()。

学习点：
    - model_validate()：Pydantic v2 的类方法，把 dict 转成 BaseModel 实例同时校验字段。
      是 TypeAdapter 的简化版，适用于已知 model 类的场景。
    - Any 类型注解：Tool 基类是泛型的，但 registry 内部存储时用 Tool[Any] 绕开类型擦除，
      让 mypy 接受而不失去工具级别的类型检查。
"""

from __future__ import annotations

from typing import Any

from my_agent.tools.base import Tool


class ToolRegistry:
    """管理所有可用工具的注册与派发。

    使用方式：
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        schemas = registry.to_anthropic_schemas()   # 传给 LLM
        result = await registry.dispatch("read_file", {"path": "a.txt"})
    """

    def __init__(self) -> None:
        # 工具名 → 工具实例的映射表
        # Tool[Any]：存储时抹去泛型参数，因为 Python 运行时不保留泛型信息（类型擦除）
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        """注册一个工具实例。

        工具实例在整个 agent 生命周期内复用（无状态，execute 只依赖入参）。

        Args:
            tool: 已实例化的 Tool 子类。name 类变量作为注册键，必须全局唯一。
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any]:
        """按名字取工具实例。

        Args:
            name: 工具名，对应 LLM tool_use block 里的 name 字段。

        Raises:
            KeyError: 工具未注册。Loop 会把这个错误包装成 tool_result 错误信息返回给 LLM。
        """
        # dict.__getitem__ 在 key 不存在时自动抛 KeyError，这里直接用即可
        return self._tools[name]

    async def dispatch(self, name: str, raw_input: dict[str, Any]) -> str:
        """校验输入并执行工具，返回字符串结果。

        这是 AgentLoop 收到 LLM tool_use block 后的唯一入口点。
        它负责两件事：
          1. 把 LLM 生成的 raw dict 转成 Pydantic 模型（校验 + 类型转换）
          2. 调用工具的 async execute()

        Args:
            name:      工具名（来自 tool_use block 的 name 字段）。
            raw_input: LLM 生成的工具参数 dict（来自 tool_use block 的 input 字段）。

        Returns:
            工具执行的字符串结果，将作为 tool_result content 返回给 LLM。

        Raises:
            KeyError:         工具名未注册（交由 AgentLoop 处理）。
            ValidationError:  raw_input 不符合工具的 input_model schema（交由 AgentLoop 处理）。
        """
        tool = self.get(name)  # 可能抛 KeyError

        # model_validate() 学习点：
        #   Pydantic v2 的类方法，等价于 Model(**raw_input) 但更显式。
        #   它会：
        #     1. 检查必填字段是否存在
        #     2. 把字段值转换为声明的类型（如 "3" → 3 for int 字段）
        #     3. 运行字段级别的 validator 函数（如果有）
        #   不符合 schema 时抛 ValidationError，包含每个字段的具体错误信息。
        validated_input = tool.input_model.model_validate(raw_input)

        # execute() 接受 input_model 的实例，在工具实现里能用 input.path 等字段访问
        return await tool.execute(validated_input)

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的 Anthropic schema 列表。

        这个列表直接传给 messages.create(tools=...) 参数，
        让 LLM 知道有哪些工具可用、每个工具接受什么参数。

        Returns:
            每项是 {"name": ..., "description": ..., "input_schema": ...} 格式的 dict。
        """
        return [tool.to_anthropic_schema() for tool in self._tools.values()]
