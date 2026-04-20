# Stage 3 · Streaming & TUI

**状态**: 🔴 not-started
**依赖**: Stage 2
**预计规模**: ~500–700 行新增 / ~150 行修改

---

## 目标

1. 把客户端从 `messages.create` 切到 `messages.stream`，让文本**逐 token 流式出现**。
2. 用 `rich` 渲染漂亮的终端 UI：工具调用用有边框的 Panel、diff 用彩色高亮、等待用 spinner。
3. 支持 **Ctrl+C 优雅中断**：取消 LLM 流、取消正在运行的工具、保留已有 history。

验收时：

```bash
uv run my-agent "写一个 fibonacci 函数并测试它"
# 文本流式涌现；bash 测试用带边框的 Panel 展示；Ctrl+C 立即停且不僵死
```

## 学习点

### Python

- **`async for` 消费流**：`async with client.messages.stream(...) as stream:`
- **`asyncio.CancelledError` 的正确处理**：何时捕获、何时重新抛出
- **`asyncio.TaskGroup`**（3.11+）或 `asyncio.gather` + cancel 级联
- **signal 处理**：`loop.add_signal_handler(SIGINT, ...)` 与 `KeyboardInterrupt` 的区别
- **`rich.live.Live`**：流式更新 panel 的模式

### Agent

- Anthropic streaming 事件序列：`message_start` → `content_block_start` → `content_block_delta` ×N → `content_block_stop` → `message_delta` → `message_stop`
- tool_use 在流式中如何逐步拼装（`input_json_delta` 事件）
- UX 原则：有反馈的等待 vs 无反馈的等待，感知时间差几个数量级

## 设计

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/my_agent/ui/__init__.py` | 占位 |
| `src/my_agent/ui/renderer.py` | `Renderer`：接收流事件 + 工具调用 + 结果，画面输出 |
| `src/my_agent/ui/diff.py` | 生成 diff 文本（edit_file 工具结果美化） |
| `tests/test_stage_3_streaming.py` | 流式事件解析单元测试（FakeLLM 产生事件序列） |
| `tests/test_stage_3_cancel.py` | 中断传播测试 |
| `evals/cases/stage-3/*.yaml` | 流式 + 中断 eval |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/client.py` | 新增 `send_stream()` 返回 `AsyncIterator[StreamEvent]`；`send()` 改为包装 stream |
| `src/my_agent/core/loop.py` | 消费流事件、处理 tool_use_partial、捕获 `CancelledError` 并清理 |
| `src/my_agent/tools/bash.py` | `CancelledError` 时 `proc.kill()` |
| `src/my_agent/__main__.py` | `signal.SIGINT` handler 挂到 event loop |

### 关键 API

```python
# core/client.py
class StreamEvent(BaseModel):
    """
    Normalized view of Anthropic streaming events. We don't want the loop
    to couple to the SDK's raw event types, so we reshape them here.
    """
    kind: Literal["text_delta", "tool_use_start", "tool_use_input_delta",
                  "tool_use_complete", "message_stop"]
    content_index: int | None = None
    text: str | None = None
    tool_name: str | None = None
    tool_id: str | None = None
    input_json_delta: str | None = None
    # ...

class AnthropicClient:
    async def send_stream(
        self, messages: list[dict], tools: list[dict]
    ) -> AsyncIterator[StreamEvent]: ...

# ui/renderer.py
class Renderer:
    def __init__(self, console: Console) -> None: ...

    def on_text_delta(self, text: str) -> None: ...
    def on_tool_use_start(self, name: str, tool_id: str) -> None: ...
    def on_tool_use_input_delta(self, partial: str) -> None: ...
    def on_tool_use_complete(self) -> None: ...
    def on_tool_result(self, tool_id: str, content: str, is_error: bool) -> None: ...
    def on_session_end(self, usage: TokenUsage, duration_s: float) -> None: ...

# core/loop.py（修改版）
class AgentLoop:
    async def run(self, user_input: str) -> str:
        history = MessageHistory()
        history.append_user(user_input)
        for turn in range(self.MAX_TURNS):
            partial = PartialResponse()
            try:
                async for event in self.client.send_stream(...):
                    self.renderer.dispatch(event)
                    partial.ingest(event)
            except asyncio.CancelledError:
                self.renderer.on_cancelled()
                raise
            history.append_assistant(partial.blocks)
            if not partial.tool_uses:
                return partial.text
            ...
```

### Ctrl+C 处理

```python
# __main__.py
async def main_async(args: Namespace) -> int:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)

    agent_task = asyncio.create_task(agent.run(args.prompt))
    stop_task = asyncio.create_task(stop.wait())

    done, pending = await asyncio.wait({agent_task, stop_task},
                                        return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        agent_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await agent_task
        return 130  # SIGINT 约定返回码
    return 0
```

Bash 工具需要响应取消：

```python
# tools/bash.py（修改版）
async def execute(self, input: BashInput) -> str:
    proc = await asyncio.create_subprocess_exec(...)
    try:
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), input.timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    return ...
```

### UI 布局示意

```
╭─ user ────────────────────────────────────────────────╮
│ 写一个 fibonacci 函数并测试它                           │
╰────────────────────────────────────────────────────────╯

Thinking...

Let me write a fibonacci function.
╭─ tool: write_file ─────────────────────────────────────╮
│ path: fib.py                                           │
│ content: def fib(n): ...                               │
╰────────────────────────────────────────────────────────╯
╭─ result ───────────────────────────────────────────────╮
│ OK: wrote 3 lines to fib.py                            │
╰────────────────────────────────────────────────────────╯

Now let's test it.
╭─ tool: bash ───────────────────────────────────────────╮
│ python -c "from fib import fib; print(fib(10))"        │
╰────────────────────────────────────────────────────────╯
╭─ result ───────────────────────────────────────────────╮
│ 55                                                     │
╰────────────────────────────────────────────────────────╯

The function works correctly. fib(10) = 55 ✓

────────────────────────────────────────────────────────
Session: 2.4k input + 0.8k output tokens (~$0.012) · 8.3s
```

## 验收标准

- [ ] 文本流式逐 token 出现（肉眼可见）
- [ ] 工具调用用带边框的 Panel 显示
- [ ] `bash` 的长运行命令（比如 `sleep 3`）用 spinner
- [ ] Ctrl+C 在任何阶段都能 ≤1 秒内终止（含 bash 执行中）
- [ ] 中断后 session 成本摘要仍然打印
- [ ] 单元测试覆盖：流事件 → history 拼装、Cancel 传播到 bash
- [ ] eval 至少 2 个 case，并通过
- [ ] mypy/ruff 全绿

## Eval Cases

- `stream-simple-echo.yaml` — 简单任务，断言 token 数 > 50（验证流式不截断）
- `tool-use-in-stream.yaml` — 任务包含工具调用，断言 tool_use + tool_result 各 ≥ 1
- `(手动)` Ctrl+C 取消测试不在 yaml eval 里（需人工），在单元测试里覆盖

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 3 段（3.1–3.7）**。

## 变更历史

- 2026-04-19：首版起草。
