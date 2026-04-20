"""Stage 0 tests — built up incrementally as sub-tasks complete.

Currently covers:
  0.1  AnthropicClient + Config
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from my_agent.config import Config, load_config
from my_agent.core.client import AnthropicClient, TokenUsage

# ---------------------------------------------------------------------------
# Task 0.1: Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_load_config_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_AGENT_LLM_API_KEY", "test-key")
        monkeypatch.setenv("MY_AGENT_LLM_BASE_URL", "http://test:9999")
        monkeypatch.setenv("MY_AGENT_MODEL", "claude-test-model")
        with patch("my_agent.config.load_dotenv"):  # skip .env file
            config = load_config()
        assert config.llm_api_key == "test-key"
        assert config.llm_base_url == "http://test:9999"
        assert config.model == "claude-test-model"

    def test_load_config_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_AGENT_LLM_API_KEY", "test-key")
        monkeypatch.delenv("MY_AGENT_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("MY_AGENT_MODEL", raising=False)
        with patch("my_agent.config.load_dotenv"):
            config = load_config()
        assert config.llm_base_url == "http://localhost:8317"
        assert config.model == "claude-sonnet-4-5-20250929"
        assert config.debug is False

    def test_debug_flag_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_AGENT_LLM_API_KEY", "k")
        monkeypatch.setenv("MY_AGENT_DEBUG", "1")
        with patch("my_agent.config.load_dotenv"):
            config = load_config()
        assert config.debug is True

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_AGENT_LLM_API_KEY", raising=False)
        with patch("my_agent.config.load_dotenv"):
            with pytest.raises(KeyError):
                load_config()

    def test_api_key_not_in_repr(self) -> None:
        config = Config(llm_api_key="super-secret-key")
        assert "super-secret-key" not in repr(config)


# ---------------------------------------------------------------------------
# Task 0.1: TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_starts_at_zero(self) -> None:
        usage = TokenUsage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0

    def test_add_accumulates(self) -> None:
        usage = TokenUsage()
        msg = MagicMock()
        msg.usage.input_tokens = 100
        msg.usage.output_tokens = 50
        usage.add(msg)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150

    def test_add_multiple_calls_accumulate(self) -> None:
        usage = TokenUsage()
        msg = MagicMock()
        msg.usage.input_tokens = 10
        msg.usage.output_tokens = 5
        usage.add(msg)
        usage.add(msg)
        assert usage.input_tokens == 20
        assert usage.output_tokens == 10
        assert usage.total_tokens == 30


# ---------------------------------------------------------------------------
# Task 0.1: AnthropicClient
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    return Config(
        llm_base_url="http://fake:1234",
        llm_api_key="test-key",
        model="claude-test",
    )


def _make_fake_message(input_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    msg = MagicMock()
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    return msg


class TestAnthropicClient:
    def test_initial_usage_is_zero(self) -> None:
        with patch("my_agent.core.client.AsyncAnthropic"):
            client = AnthropicClient(_make_config())
        assert client.usage.input_tokens == 0
        assert client.usage.output_tokens == 0

    async def test_send_accumulates_usage(self) -> None:
        fake_msg = _make_fake_message(10, 5)
        with patch("my_agent.core.client.AsyncAnthropic") as mock_cls:
            mock_inner = MagicMock()
            mock_inner.messages.create = AsyncMock(return_value=fake_msg)
            mock_cls.return_value = mock_inner

            client = AnthropicClient(_make_config())
            await client.send([], [])
            assert client.usage.input_tokens == 10
            assert client.usage.output_tokens == 5

            await client.send([], [])
            assert client.usage.input_tokens == 20
            assert client.usage.output_tokens == 10

    async def test_send_returns_message(self) -> None:
        fake_msg = _make_fake_message()
        with patch("my_agent.core.client.AsyncAnthropic") as mock_cls:
            mock_inner = MagicMock()
            mock_inner.messages.create = AsyncMock(return_value=fake_msg)
            mock_cls.return_value = mock_inner

            client = AnthropicClient(_make_config())
            result = await client.send([], [])
        assert result is fake_msg

    def test_usage_property_returns_token_usage_instance(self) -> None:
        with patch("my_agent.core.client.AsyncAnthropic"):
            client = AnthropicClient(_make_config())
        assert isinstance(client.usage, TokenUsage)


# ---------------------------------------------------------------------------
# Task 0.2: MessageHistory
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from my_agent.core.history import MessageHistory  # noqa: E402


def _text_block(text: str) -> SimpleNamespace:
    """模拟 Anthropic SDK TextBlock（只需 .type 和 .text 属性）。"""
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id: str, name: str, input: dict) -> SimpleNamespace:
    """模拟 Anthropic SDK ToolUseBlock（只需 .type/.id/.name/.input 属性）。"""
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


class TestMessageHistory:
    def test_initial_state_is_empty(self) -> None:
        h = MessageHistory()
        assert h.to_anthropic() == []
        assert len(h) == 0

    def test_append_user_text(self) -> None:
        h = MessageHistory()
        h.append_user("hello")
        msgs = h.to_anthropic()
        assert len(msgs) == 1
        assert msgs[0] == {"role": "user", "content": "hello"}

    def test_append_assistant_text_block(self) -> None:
        h = MessageHistory()
        h.append_assistant([_text_block("I will help.")])
        msgs = h.to_anthropic()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == [{"type": "text", "text": "I will help."}]

    def test_append_assistant_tool_use_block(self) -> None:
        h = MessageHistory()
        h.append_assistant([_tool_use_block("tu_1", "read_file", {"path": "a.txt"})])
        content = h.to_anthropic()[0]["content"]
        assert content == [
            {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "a.txt"}}
        ]

    def test_append_assistant_mixed_blocks(self) -> None:
        h = MessageHistory()
        h.append_assistant([
            _text_block("Let me read that."),
            _tool_use_block("tu_1", "read_file", {"path": "a.txt"}),
        ])
        content = h.to_anthropic()[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"

    def test_append_tool_result_creates_user_message(self) -> None:
        h = MessageHistory()
        h.append_tool_result("tu_1", "file contents here")
        msgs = h.to_anthropic()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file contents here"}
        ]

    def test_append_tool_result_with_error_flag(self) -> None:
        h = MessageHistory()
        h.append_tool_result("tu_1", "file not found", is_error=True)
        block = h.to_anthropic()[0]["content"][0]
        assert block["is_error"] is True

    def test_append_tool_result_no_error_flag_when_false(self) -> None:
        """is_error=False の場合は is_error キーを送らない（余分なフィールド排除）。"""
        h = MessageHistory()
        h.append_tool_result("tu_1", "ok")
        block = h.to_anthropic()[0]["content"][0]
        assert "is_error" not in block

    def test_multiple_tool_results_grouped_in_one_user_message(self) -> None:
        """Anthropic 要求同一轮的多个 tool_result 放在同一条 user 消息里。"""
        h = MessageHistory()
        h.append_assistant([
            _tool_use_block("tu_1", "read_file", {"path": "a.txt"}),
            _tool_use_block("tu_2", "read_file", {"path": "b.txt"}),
        ])
        h.append_tool_result("tu_1", "content of a")
        h.append_tool_result("tu_2", "content of b")

        msgs = h.to_anthropic()
        assert len(msgs) == 2  # assistant + 1 user（不是 3）
        user_content = msgs[1]["content"]
        assert len(user_content) == 2
        assert user_content[0]["tool_use_id"] == "tu_1"
        assert user_content[1]["tool_use_id"] == "tu_2"

    def test_full_conversation_shape(self) -> None:
        """模拟完整的一轮对话：user → assistant[tool_use] → user[tool_result] → assistant[text]。"""
        h = MessageHistory()
        h.append_user("读 a.txt")
        h.append_assistant([_tool_use_block("tu_1", "read_file", {"path": "a.txt"})])
        h.append_tool_result("tu_1", "hello world")
        h.append_assistant([_text_block("文件内容是：hello world")])

        msgs = h.to_anthropic()
        assert len(msgs) == 4
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[3]["role"] == "assistant"

    def test_to_anthropic_returns_copy(self) -> None:
        """to_anthropic() 返回的列表是副本，修改它不影响内部状态。"""
        h = MessageHistory()
        h.append_user("hi")
        copy = h.to_anthropic()
        copy.clear()
        assert len(h) == 1  # 内部未受影响


# ---------------------------------------------------------------------------
# Task 0.3: Tool base class
# ---------------------------------------------------------------------------

from pydantic import BaseModel  # noqa: E402

from my_agent.tools.base import Tool  # noqa: E402


# 定义两个用于测试的具体工具类 —— 验证泛型基类的子类化用法
class _EchoInput(BaseModel):
    message: str
    repeat: int = 1


class _EchoTool(Tool[_EchoInput]):
    """最简具体工具，把 message 重复 repeat 次返回。"""
    name = "echo"
    description = "Echo the message N times."
    input_model = _EchoInput

    async def execute(self, input: _EchoInput) -> str:
        return input.message * input.repeat


class _NoArgs(BaseModel):
    """空输入模型（无字段）。Pydantic 不允许直接对 BaseModel 本身调用 model_json_schema()，
    所以边界情况要用真正的子类而不是基类。"""
    pass


class _NoInputTool(Tool[_NoArgs]):
    """输入为空 _NoArgs 的工具，验证无参数边界情况。"""
    name = "noop"
    description = "Do nothing."
    input_model = _NoArgs

    async def execute(self, input: _NoArgs) -> str:
        return "ok"


class TestToolBase:
    def test_concrete_tool_can_be_instantiated(self) -> None:
        tool = _EchoTool()
        assert tool is not None

    def test_abstract_tool_cannot_be_instantiated(self) -> None:
        """Tool 是抽象基类，直接实例化应抛出 TypeError。"""
        import pytest
        with pytest.raises(TypeError):
            Tool()  # type: ignore[abstract]

    def test_to_anthropic_schema_keys(self) -> None:
        schema = _EchoTool.to_anthropic_schema()
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema

    def test_to_anthropic_schema_name_and_description(self) -> None:
        schema = _EchoTool.to_anthropic_schema()
        assert schema["name"] == "echo"
        assert schema["description"] == "Echo the message N times."

    def test_to_anthropic_schema_input_schema_matches_pydantic(self) -> None:
        """input_schema 必须等于 Pydantic model_json_schema()，不能手写。"""
        schema = _EchoTool.to_anthropic_schema()
        assert schema["input_schema"] == _EchoInput.model_json_schema()

    def test_to_anthropic_schema_contains_field_info(self) -> None:
        """input_schema 里要有 message 和 repeat 字段的类型信息。"""
        schema = _EchoTool.to_anthropic_schema()
        props = schema["input_schema"]["properties"]
        assert "message" in props
        assert "repeat" in props

    async def test_execute_returns_string(self) -> None:
        tool = _EchoTool()
        result = await tool.execute(_EchoInput(message="hi", repeat=3))
        assert result == "hihihi"

    def test_class_variables_accessible_on_class(self) -> None:
        """name/description/input_model 是类变量，不需要实例化就能访问。"""
        assert _EchoTool.name == "echo"
        assert _EchoTool.input_model is _EchoInput


# ---------------------------------------------------------------------------
# Task 0.4: ToolRegistry
# ---------------------------------------------------------------------------

from my_agent.tools.registry import ToolRegistry  # noqa: E402


class TestToolRegistry:
    def _make_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(_EchoTool())
        registry.register(_NoInputTool())
        return registry

    def test_register_and_get(self) -> None:
        """register 之后能用 get(name) 取回工具实例。"""
        registry = self._make_registry()
        tool = registry.get("echo")
        assert isinstance(tool, _EchoTool)

    def test_get_unknown_raises_key_error(self) -> None:
        """get 不存在的工具名应抛出 KeyError（由 loop 处理）。"""
        registry = ToolRegistry()
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_to_anthropic_schemas_returns_list(self) -> None:
        """to_anthropic_schemas() 应返回所有已注册工具的 schema 列表。"""
        registry = self._make_registry()
        schemas = registry.to_anthropic_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 2

    def test_to_anthropic_schemas_contains_tool_names(self) -> None:
        registry = self._make_registry()
        names = {s["name"] for s in registry.to_anthropic_schemas()}
        assert "echo" in names
        assert "noop" in names

    async def test_dispatch_valid_input(self) -> None:
        """dispatch 用正确参数调用工具，返回 execute() 的字符串结果。"""
        registry = self._make_registry()
        result = await registry.dispatch("echo", {"message": "hi", "repeat": 2})
        assert result == "hihi"

    async def test_dispatch_unknown_tool_raises_key_error(self) -> None:
        """dispatch 未注册工具名 → KeyError，让 loop 决定如何处理。"""
        registry = ToolRegistry()
        with pytest.raises(KeyError):
            await registry.dispatch("ghost", {})

    async def test_dispatch_invalid_input_raises_validation_error(self) -> None:
        """dispatch 输入不符合 schema → ValidationError，让 loop 决定如何处理。"""
        from pydantic import ValidationError
        registry = self._make_registry()
        with pytest.raises(ValidationError):
            await registry.dispatch("echo", {"repeat": "not-an-int"})  # message 缺失


# ---------------------------------------------------------------------------
# Task 0.5: ReadFileTool
# ---------------------------------------------------------------------------

from my_agent.tools.read_file import ReadFileInput, ReadFileTool  # noqa: E402


class TestReadFileTool:
    def test_schema_name(self) -> None:
        assert ReadFileTool.name == "read_file"

    def test_schema_has_path_field(self) -> None:
        props = ReadFileTool.to_anthropic_schema()["input_schema"]["properties"]
        assert "path" in props

    async def test_reads_existing_file(self, tmp_path: pytest.TempPath) -> None:
        """读取真实文件，验证内容正确返回。"""
        f = tmp_path / "hello.txt"
        f.write_text("hello world\n", encoding="utf-8")

        tool = ReadFileTool()
        result = await tool.execute(ReadFileInput(path=str(f)))
        assert "hello world" in result

    async def test_dispatch_via_registry(self, tmp_path: pytest.TempPath) -> None:
        """通过 ToolRegistry dispatch，验证与 registry 集成无误。"""
        f = tmp_path / "test.txt"
        f.write_text("registry integration", encoding="utf-8")

        registry = ToolRegistry()
        registry.register(ReadFileTool())
        result = await registry.dispatch("read_file", {"path": str(f)})
        assert "registry integration" in result

    async def test_nonexistent_file_raises(self) -> None:
        """读取不存在的文件应抛出 FileNotFoundError（由 loop 处理为 tool_result error）。"""
        tool = ReadFileTool()
        with pytest.raises(FileNotFoundError):
            await tool.execute(ReadFileInput(path="/nonexistent/path/does/not/exist.txt"))


# ---------------------------------------------------------------------------
# Task 0.6: AgentLoop
# ---------------------------------------------------------------------------

import logging  # noqa: E402

from my_agent.core.loop import AgentLoop  # noqa: E402
from tests.conftest import FakeLLM, fake_message, fake_text_block, fake_tool_use_block  # noqa: E402


def _make_loop(scripted_turns: list) -> AgentLoop:
    """创建 AgentLoop 实例，使用 FakeLLM 和预注册的 ReadFileTool。"""
    llm = FakeLLM(scripted_turns=scripted_turns)
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    logger = logging.getLogger("test")
    return AgentLoop(client=llm, registry=registry, logger=logger)  # type: ignore[arg-type]


class TestAgentLoop:
    async def test_text_only_response_returns_text(self) -> None:
        """LLM 直接返回文本（无 tool_use），loop 应立即返回该文本。"""
        loop = _make_loop([
            fake_message([fake_text_block("直接回答，无需工具。")]),
        ])
        result = await loop.run("你好")
        assert result == "直接回答，无需工具。"

    async def test_tool_use_then_text(self, tmp_path: pytest.TempPath) -> None:
        """完整一轮工具调用：tool_use → 真实执行 → tool_result → 最终文本。"""
        f = tmp_path / "data.txt"
        f.write_text("hello from file", encoding="utf-8")

        loop = _make_loop([
            # 第一轮：LLM 要求调用 read_file
            fake_message([fake_tool_use_block("tu_1", "read_file", {"path": str(f)})]),
            # 第二轮：LLM 基于工具结果给出最终回答
            fake_message([fake_text_block("文件内容：hello from file")]),
        ])
        result = await loop.run("读一下那个文件")
        assert "hello from file" in result

    async def test_tool_error_is_returned_as_is_error(self) -> None:
        """工具执行失败时，loop 应把错误包装成 is_error=True 的 tool_result，
        而不是让整个 loop 崩溃。LLM 会收到错误信息并自行决定下一步。"""
        loop = _make_loop([
            # 第一轮：LLM 要求读一个不存在的文件
            fake_message([fake_tool_use_block("tu_1", "read_file", {"path": "/no/such/file.txt"})]),
            # 第二轮：LLM 收到错误后给出回复
            fake_message([fake_text_block("文件不存在，无法读取。")]),
        ])
        result = await loop.run("读那个文件")
        # loop 不应抛出异常，应正常返回最终文本
        assert "无法读取" in result

    async def test_multiple_tool_uses_in_one_turn(self, tmp_path: pytest.TempPath) -> None:
        """LLM 一轮内请求多个工具调用，loop 应全部执行并把结果分组进同一条 user 消息。"""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa", encoding="utf-8")
        f2.write_text("bbb", encoding="utf-8")

        loop = _make_loop([
            # 第一轮：同时调用两个工具
            fake_message([
                fake_tool_use_block("tu_1", "read_file", {"path": str(f1)}),
                fake_tool_use_block("tu_2", "read_file", {"path": str(f2)}),
            ]),
            # 第二轮：LLM 基于两个工具结果回复
            fake_message([fake_text_block("两个文件都读到了：aaa 和 bbb")]),
        ])
        result = await loop.run("同时读两个文件")
        assert "aaa" in result and "bbb" in result

    async def test_llm_called_twice_for_tool_use(self, tmp_path: pytest.TempPath) -> None:
        """验证 loop 的轮次数：一次 tool_use 应导致 LLM 被调用两次。"""
        f = tmp_path / "x.txt"
        f.write_text("x", encoding="utf-8")

        llm = FakeLLM(scripted_turns=[
            fake_message([fake_tool_use_block("tu_1", "read_file", {"path": str(f)})]),
            fake_message([fake_text_block("done")]),
        ])
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        loop = AgentLoop(client=llm, registry=registry, logger=logging.getLogger("t"))  # type: ignore[arg-type]
        await loop.run("读文件")
        assert llm.call_count == 2  # 第 1 轮 tool_use，第 2 轮最终文本
