"""
Anthropic API 客户端封装。

架构角色：
    整个项目唯一可以 `import anthropic` 的模块。
    AgentLoop 通过 AnthropicClient.send() 与 LLM 通信，其余模块不感知 SDK 细节。
    这一层的存在让未来切换 LLM 提供商时只需改这一个文件。

学习点：
    - @dataclass：Python 3.7+ 的数据类装饰器，自动生成 __init__/__repr__/__eq__
    - Generic + TypeVar：Python 泛型，让容器/基类携带类型参数（本文件间接使用）
    - cast()：mypy 类型收窄工具，不产生运行时开销，只影响静态分析
    - Anthropic Message 类型：SDK 提供完整类型注解，可以放心用于 mypy --strict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

# AsyncAnthropic：异步版客户端，所有方法都返回 coroutine，需要 await
from anthropic import AsyncAnthropic

# 从 anthropic.types 导入具体类型，用于静态类型检查
# Message：LLM 返回的完整响应对象
# MessageParam：发给 LLM 的单条消息（TypedDict 格式：{"role": "user", "content": "..."}）
# ToolParam：工具定义（name + description + input_schema）
from anthropic.types import Message, MessageParam, ToolParam

from my_agent.config import Config


@dataclass
class TokenUsage:
    """累计 token 使用量，跨多轮对话持续追踪。

    设计原因：
        用 @dataclass 而不是普通 class，是因为它只是数据容器，
        @dataclass 自动生成 __init__ 和 __repr__，不需要手写样板代码。

        input_tokens：发给模型的 token 数（prompt）
        output_tokens：模型生成的 token 数（completion）
        两者计费方式不同，分别记录方便成本分析。

    @dataclass 学习点：
        字段的默认值直接写在类体里（= 0），@dataclass 会把它们放进 __init__。
        等价于手写：def __init__(self, input_tokens=0, output_tokens=0): ...
    """

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, message: Message) -> None:
        """从一次 LLM 响应里累加 token 数。

        Args:
            message: Anthropic SDK 返回的 Message 对象，
                     其 .usage.input_tokens / .output_tokens 是本轮消耗量。
        """
        self.input_tokens += message.usage.input_tokens
        self.output_tokens += message.usage.output_tokens

    @property
    def total_tokens(self) -> int:
        """输入 + 输出的总 token 数。

        @property 学习点：
            让调用方用 usage.total_tokens 而不是 usage.total_tokens()，
            对外看起来像普通属性，内部却是方法（可以做计算）。
        """
        return self.input_tokens + self.output_tokens


class AnthropicClient:
    """AsyncAnthropic 的薄封装，额外提供 token 累计追踪。

    职责边界：
        - 构造 AsyncAnthropic 实例（传入 base_url + api_key）
        - 代理 messages.create() 调用，透明累加 token 计数
        - 不做消息格式转换（那是 MessageHistory 的职责）
        - 不做重试/限速（暂时超出 Stage 0 范围）

    为什么封装而不直接用 SDK：
        1. 集中管理 token 统计，不用在每个调用点都手写累加
        2. 如果将来切换 LLM 提供商，只改这个类的实现，调用方不感知
        3. 测试时只需 mock 这个类，不用 mock 整个 anthropic SDK
    """

    def __init__(self, config: Config) -> None:
        # 保存 SDK 客户端实例（私有，外部不直接访问）
        # base_url 指向本地 CPA 代理；api_key 是 CPA 配置里的值
        self._inner = AsyncAnthropic(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
        )
        self._model = config.model

        # 初始化空的 token 计数器，随每次 send() 调用累积
        self._usage = TokenUsage()

    async def send(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Message:
        """向 Anthropic API 发送一轮对话请求，累计 token 使用量。

        Anthropic 协议说明：
            messages 是完整的对话历史，格式为：
              [{"role": "user", "content": "..."}, {"role": "assistant", "content": [...]}, ...]
            每次调用都要把全部历史一起发过去（无状态 API，服务端不保存历史）。
            tools 是工具定义列表，让模型知道它可以调用哪些工具。

        类型说明：
            参数用 list[dict[str, Any]] 而不是 list[MessageParam]，
            因为 MessageParam 是 TypedDict，mypy 不允许普通 dict 直接赋值给它。
            在调用 SDK 时用 cast() 告诉 mypy："我知道这个 dict 符合 MessageParam 结构"。

        cast() 学习点：
            cast(T, value) 在运行时什么都不做（直接返回 value），
            它只是给 mypy 一个类型断言，让静态检查通过。
            适用场景：你比 mypy 更了解某个值的实际类型时。

        Args:
            messages: 对话历史，Anthropic messages 格式的 dict 列表。
            tools: 工具定义列表，每项含 name/description/input_schema。

        Returns:
            Anthropic Message 对象，含 .content（内容块列表）和 .usage（token 数）。
        """
        response = await self._inner.messages.create(
            model=self._model,
            # max_tokens：模型输出上限；不设会报错；8096 是 Claude 的常用上限
            max_tokens=8096,
            # cast()：把 list[dict] 声明为 list[MessageParam]，仅影响类型检查
            messages=cast(list[MessageParam], messages),
            tools=cast(list[ToolParam], tools),
        )
        # 每次调用后立即累加，保证 usage 始终是最新总量
        self._usage.add(response)
        return response

    @property
    def usage(self) -> TokenUsage:
        """返回从实例创建到现在的累计 token 使用量（只读）。"""
        return self._usage
