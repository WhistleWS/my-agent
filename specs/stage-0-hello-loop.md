# Stage 0 · Hello Loop

**状态**: 🟡 in-progress
**依赖**: 无（首阶段）
**预计规模**: ~400–600 行新增

---

## 目标

建立最小可运行的 agent 主循环。能接收用户输入 → 调用 Claude → 解析一个 `tool_use` → 执行一个工具（`read_file`） → 把 `tool_result` 喂回 → 拿到最终回复。

验收时要能跑通：

```bash
uv run my-agent "告诉我 pyproject.toml 的前 5 行"
```

agent 应依次：`tool_use read_file` → 读取文件 → `tool_result` → 最终文本回答。

## 学习点

### Python

- **uv**：`uv init`、`uv add`、`uv sync`、`uv run`、`uv lock`
- **Pydantic v2**：`BaseModel`、`ConfigDict`、`model_json_schema()`、字段校验
- **泛型**：`TypeVar`、`Generic[T]`、`ClassVar`
- **async**：`asyncio.run()`、`async def`、`await`、async 生命周期
- **src-layout**：`src/package/__init__.py` + `pyproject.toml` 指向
- **argparse** 基础

### Agent

- Anthropic `messages.create` 的 **tool-use 双向协议**：`tool_use` content block + `tool_result` content block 的对齐 id
- 消息历史的"滚雪球"规律：`user → assistant[tool_use] → user[tool_result] → assistant[text]`
- 为什么工具输入必须是 JSON Schema —— LLM 需要 schema 才能正确生成调用参数

## 设计

### 新增文件

| 文件 | 职责 | LoC 估算 |
|---|---|---|
| `src/my_agent/__init__.py` | 包占位 | 1 |
| `src/my_agent/config.py` | 读 `.env`；`Config` Pydantic 模型；启动健康检查 | ~70 |
| `src/my_agent/core/__init__.py` | 占位 | 1 |
| `src/my_agent/core/client.py` | `AnthropicClient`（`AsyncAnthropic` 薄封装 + token 累加） | ~90 |
| `src/my_agent/core/history.py` | `MessageHistory`：append/to_anthropic | ~70 |
| `src/my_agent/core/loop.py` | `AgentLoop.run(user_input)` 最小循环 | ~110 |
| `src/my_agent/tools/__init__.py` | 占位 | 1 |
| `src/my_agent/tools/base.py` | `Tool[InputT]` 基类 + `to_anthropic_schema()` | ~70 |
| `src/my_agent/tools/registry.py` | `ToolRegistry`：注册 + 派发 | ~50 |
| `src/my_agent/tools/read_file.py` | 第一个工具 | ~30 |
| `src/my_agent/__main__.py` | argparse + `asyncio.run(...)` | ~50 |
| `tests/__init__.py` | 占位 | 1 |
| `tests/conftest.py` | `FakeLLM` + 常用 fixture | ~120 |
| `tests/test_stage_0_hello_loop.py` | 端到端一轮往返 | ~100 |

### 关键 API（签名草案）

```python
# config.py
class Config(BaseModel):
    llm_base_url: str
    llm_api_key: str = Field(repr=False)   # 避免 log 泄露
    model: str
    debug: bool = False
    log_level: str = "INFO"

def load_config() -> Config: ...
async def health_check(config: Config) -> None: ...  # GET {base_url}/v1/models

# core/client.py
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

class AnthropicClient:
    def __init__(self, config: Config) -> None: ...
    async def send(
        self, messages: list[dict], tools: list[dict]
    ) -> anthropic.types.Message: ...
    @property
    def usage(self) -> TokenUsage: ...

# core/history.py
class MessageHistory:
    def __init__(self) -> None:
        self._messages: list[dict] = []
    def append_user(self, content: str) -> None: ...
    def append_assistant(self, blocks: list[ContentBlock]) -> None: ...
    def append_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None: ...
    def to_anthropic(self) -> list[dict]: ...

# tools/base.py
InputT = TypeVar("InputT", bound=BaseModel)

class Tool(Generic[InputT], ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    @abstractmethod
    async def execute(self, input: InputT) -> str: ...

    @classmethod
    def to_anthropic_schema(cls) -> dict:
        return {
            "name": cls.name,
            "description": cls.description,
            "input_schema": cls.input_model.model_json_schema(),
        }

# tools/registry.py
class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    async def dispatch(self, name: str, raw_input: dict) -> str:
        """校验输入 → 执行。KeyError / ValidationError 上抛给 loop 处理。"""
    def to_anthropic_schemas(self) -> list[dict]: ...

# tools/read_file.py
class ReadFileInput(BaseModel):
    path: str

class ReadFileTool(Tool[ReadFileInput]):
    name = "read_file"
    description = "Read the contents of a file at the given path relative to the cwd."
    input_model = ReadFileInput

    async def execute(self, input: ReadFileInput) -> str:
        return Path(input.path).read_text(encoding="utf-8")

# core/loop.py
class AgentLoop:
    def __init__(
        self, client: AnthropicClient, registry: ToolRegistry, logger: Logger
    ) -> None: ...

    async def run(self, user_input: str) -> str:
        history = MessageHistory()
        history.append_user(user_input)
        while True:
            response = await self.client.send(
                history.to_anthropic(),
                self.registry.to_anthropic_schemas(),
            )
            history.append_assistant(response.content)
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return _extract_text(response.content)
            for tu in tool_uses:
                try:
                    result = await self.registry.dispatch(tu.name, tu.input)
                    history.append_tool_result(tu.id, result)
                except Exception as e:
                    history.append_tool_result(tu.id, str(e), is_error=True)
```

**Stage 0 明确不做的事**（留给后续阶段）：

- `max_turns` 保护（Stage 1）
- 工具结果截断（Stage 1）
- 权限层（Stage 2）
- 流式（Stage 3）
- 系统 prompt 注入（Stage 4）

### 数据流（时序）

```
CLI             AgentLoop      AnthropicClient    ToolRegistry    ReadFileTool
 │                 │                │                  │                │
 │─user_input─────▶│                │                  │                │
 │                 │─send(msgs)────▶│                  │                │
 │                 │◄──response─────│                  │                │
 │                 │                │                  │                │
 │                 │─dispatch──────▶│─────────────────▶│───────────────▶│
 │                 │◄─tool_result───│                  │                │
 │                 │                │                  │                │
 │                 │─send(msgs+tr)─▶│                  │                │
 │                 │◄──final text───│                  │                │
 │◄──final text────│                │                  │                │
```

### CPA 启动方式（附录）

```bash
/Users/whistle/Desktop/CLIProxyAPI_6.9.26_darwin_arm64/cli-proxy-api
```

默认监听 `:8317`。打开 `http://localhost:8317/management.html` 看真实可用模型。
`my-agent` 启动时会 `GET /v1/models` 做健康检查，失败即报错退出。

### 切回官方 API（附录）

```bash
# .env
MY_AGENT_LLM_BASE_URL=https://api.anthropic.com
MY_AGENT_LLM_API_KEY=sk-ant-...
MY_AGENT_MODEL=claude-sonnet-4-5-20250929
```

零代码改动。

## 验收标准

- [ ] `uv sync` 安装依赖无错
- [ ] `uv run pytest` 全绿
- [ ] `uv run ruff check && uv run ruff format --check && uv run mypy src` 全绿
- [ ] Demo（CPA 已启动 + `.env` 填好）：
  ```bash
  uv run my-agent "告诉我 pyproject.toml 的前 5 行"
  ```
  返回包含 `[project]` 和 `name = "my-agent"` 的文本
- [ ] `MY_AGENT_DEBUG=1 uv run my-agent "..."` 打印完整 prompt 与原始响应
- [ ] CPA 未启动时，启动即给明确错误：`CPA 健康检查失败：http://localhost:8317/v1/models 不可达`
- [ ] 在 `tests/test_stage_0_hello_loop.py` 中，用 `FakeLLM` 模拟一轮 `tool_use(read_file)` → `tool_result` → 最终文本，断言 history 的形状正确

## Eval Cases

Stage 0 不写 eval case（eval harness 要等 Stage 1 建）。

**占位**：Stage 1 完成时回填一个 sanity case：
```yaml
# evals/cases/stage-1/read-a-file.yaml（Stage 1 引入）
name: read-a-file
task: "告诉我 pyproject.toml 的前 5 行"
assertions:
  - tool_called: {name: read_file, times: ">=1"}
  - final_response_contains: "[project]"
```

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 0 段（0.1–0.9）**。

## 变更历史

- 2026-04-19：首版起草。
- 2026-04-19：完成 0.1 AsyncAnthropic 薄封装（`config.py` + `core/client.py`，12 tests）。
- 2026-04-19：完成 0.2 MessageHistory（`core/history.py`，11 tests，含工具结果分组验证）。
- 2026-04-19：完成 0.3 Tool 基类（`tools/base.py`，8 tests，Python 3.12 PEP 695 泛型语法）。
- 2026-04-19：完成 0.4 ToolRegistry（`tools/registry.py`，7 tests，model_validate() dispatch）。
- 2026-04-19：完成 0.5 ReadFileTool（`tools/read_file.py`，5 tests，pathlib + ASYNC240 noqa）。
- 2026-04-19：完成 0.6 AgentLoop（`core/loop.py`，5 tests）+ 0.8 FakeLLM（`tests/conftest.py`）+ 0.9 整合测试（共 48 tests）。
