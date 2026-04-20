"""
工具基类模块。

架构角色：
    所有工具（read_file、bash、edit_file 等）都继承自这里的 Tool[InputT]。
    ToolRegistry 依赖 Tool 基类来统一注册和派发工具调用。
    to_anthropic_schema() 把工具定义转成 Anthropic API 要求的 JSON Schema 格式，
    让 LLM 知道有哪些工具可用、每个工具接受什么参数。

    数据流位置：
        Tool 子类定义 → ToolRegistry 收集 → to_anthropic_schemas() →
        AnthropicClient.send(tools=...) → LLM 生成 tool_use block →
        ToolRegistry.dispatch() → Tool.execute()

学习点：
    - TypeVar + Generic：Python 泛型，让基类携带子类的具体输入类型
    - ABC + abstractmethod：强制子类实现 execute()，否则无法实例化
    - ClassVar：声明类级别属性（不属于实例），子类以类变量形式覆盖
    - model_json_schema()：Pydantic v2 方法，从 BaseModel 生成 JSON Schema
      （这是 Anthropic tool use 的核心——LLM 用 schema 来生成合法的工具调用参数）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel


class Tool[InputT: BaseModel](ABC):
    """所有工具的抽象基类，定义工具的统一接口和 Anthropic schema 生成。

    PEP 695 类型参数语法（Python 3.12+）学习点：
        `class Tool[InputT: BaseModel]` 是 Python 3.12 引入的新泛型语法（PEP 695）。
        等价于旧写法 `class Tool(Generic[InputT], ABC)` + `InputT = TypeVar("InputT", bound=BaseModel)`。
        `InputT: BaseModel` 表示约束（bound），即 InputT 只能是 BaseModel 或其子类。
        Tool[ReadFileInput]、Tool[BashInput] 等是同一个基类的不同"实例化"。
        这让 mypy 知道 ReadFileTool.execute() 接受 ReadFileInput，
        BashTool.execute() 接受 BashInput，而不是混用。
        Python 运行时不强制泛型类型（擦除语义），但 mypy 静态检查会。

    ABC 学习点：
        ABC（Abstract Base Class）使类变成抽象类。
        标有 @abstractmethod 的方法必须被子类覆盖，否则子类实例化时抛 TypeError。
        这是 Python 实现"接口"（interface）的标准方式。

    ClassVar 学习点：
        ClassVar[str] 声明 name/description 是类变量，不是实例变量。
        子类用 name = "read_file" 这样的类变量赋值来覆盖。
        mypy 会检查子类是否提供了这些类变量（如果缺少会在使用时报错）。

    子类必须定义的三个类变量：
        name:        Anthropic tool_use block 里的工具名（必须唯一）
        description: 告诉 LLM 这个工具的用途（影响 LLM 是否选择调用它）
        input_model: Pydantic BaseModel 子类，定义工具的输入参数结构
    """

    # ClassVar[str]：类变量声明，子类必须赋值，否则 to_anthropic_schema() 调用时会 AttributeError
    name: ClassVar[str]
    description: ClassVar[str]

    # ClassVar[type[BaseModel]]：存储的是"类本身"而不是"类的实例"
    # 例如：input_model = ReadFileInput（类对象）而不是 ReadFileInput()（实例）
    input_model: ClassVar[type[BaseModel]]

    @abstractmethod
    async def execute(self, input: InputT) -> str:
        """执行工具逻辑，返回字符串结果。

        @abstractmethod 学习点：
            子类必须覆盖此方法，否则无法实例化（Python 会在 __init__ 时抛 TypeError）。
            async def：工具执行是 I/O 密集型（读文件、跑命令），用 async 让事件循环
            在等待时可以切换到其他任务，避免阻塞整个 agent 主循环。

        Args:
            input: 经过 Pydantic 校验的工具输入（类型由子类泛型参数决定）。

        Returns:
            工具执行结果的字符串表示，会作为 tool_result 返回给 LLM。
            出错时应抛出异常，由 AgentLoop 捕获并包装成 is_error=True 的 tool_result。
        """
        ...

    @classmethod
    def to_anthropic_schema(cls) -> dict[str, Any]:
        """生成 Anthropic tool use API 要求的工具定义 dict。

        Anthropic tool 定义格式：
            {
                "name": "read_file",
                "description": "Read the contents of a file...",
                "input_schema": {          ← 这是 JSON Schema 格式
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "..."}
                    },
                    "required": ["path"]
                }
            }

        LLM 用这个 schema 来：
            1. 了解工具能做什么（description）
            2. 知道调用时需要传哪些参数（input_schema）
            3. 生成符合 schema 的 tool_use block（LLM 会自动填参数）

        @classmethod 学习点：
            cls 是"类本身"而不是实例（self 才是实例）。
            这允许直接用 ReadFileTool.to_anthropic_schema() 调用，不需要先 new 一个实例。
            ToolRegistry 收集所有工具的 schema 时正是这样批量调用的。

        model_json_schema() 学习点：
            Pydantic v2 的方法，把 BaseModel 子类的字段定义转成标准 JSON Schema。
            例如 ReadFileInput(path: str) → {"type": "object", "properties": {"path": {"type": "string"}}}
            这是"不要手写 schema"的关键——Pydantic 自动从类型注解生成，永远和代码保持同步。

        Returns:
            符合 Anthropic messages API 的工具定义 dict，可直接放入 tools 列表。
        """
        return {
            "name": cls.name,
            "description": cls.description,
            # model_json_schema() 是 Pydantic v2 的类方法，返回 JSON Schema dict
            "input_schema": cls.input_model.model_json_schema(),
        }
