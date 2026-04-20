# Stage 1 · Core Tools + Eval Harness

**状态**: 🔴 not-started
**依赖**: Stage 0
**预计规模**: ~800–1000 行新增 / ~150 行修改

---

## 目标

两件事：

1. **补齐核心工具集**：`write_file`、`edit_file`、`bash`、`glob`、`grep` —— 让 agent 拥有日常操作代码库的五种基本动作。
2. **建立 eval harness**：一个可跑 yaml case、用规则断言 + 可选 LLM-as-judge 评估端到端行为的工具链。这是**整个项目后续每阶段都依赖**的基础设施。

加上对主循环的鲁棒性强化（`max_turns`、工具错误回传、结果截断）。

验收时 agent 应能完成：

```bash
uv run my-agent "读 README.md，把第一段翻译成英文，写到 README.en.md"
uv run my-agent eval --stage 1       # 2–3 个 case 全绿
```

## 学习点

### Python

- **`asyncio.create_subprocess_exec`** + 超时：正确的 async 子进程
- **`pathlib`**：现代路径处理（避免裸字符串）
- Python 异常层级：`Exception` / `BaseException` / 自定义异常
- **YAML** 解析：`yaml.safe_load` 的使用与风险
- 子命令 argparse（`parser.add_subparsers`）

### Agent

- **错误回传**：工具抛异常时如何用 `is_error=True` 的 `tool_result` 喂回，让模型自行修正
- **结果截断策略**：尾部保留 + `[truncated N chars]` 提示（避免工具输出爆炸 context）
- **max_turns 保护**：无限循环是 agent 最常见的失败模式
- **Eval vs 单元测试**：单元测试证明"代码正确"；eval 证明"行为正确"
- **LLM-as-judge**：用一个"评委"Claude 给另一个 Claude 的输出打分 —— prompt 工程的基础模式

## 设计

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/my_agent/tools/write_file.py` | 覆盖写文件（`overwrite: bool` 参数） |
| `src/my_agent/tools/edit_file.py` | 精确字符串替换（`old_string` → `new_string`，验证唯一性） |
| `src/my_agent/tools/bash.py` | `asyncio.create_subprocess_exec`，超时默认 30s，stdout/stderr 合并 |
| `src/my_agent/tools/glob.py` | `pathlib.Path.glob`，返回匹配路径列表 |
| `src/my_agent/tools/grep.py` | 基于 `ripgrep`（优先）或 Python 正则 fallback |
| `evals/harness.py` | Case 加载、run、断言执行、trace 收集 |
| `evals/judge.py` | LLM-as-judge（`rubric` → 0-10 评分 + 理由） |
| `evals/cases/stage-1/read-and-translate.yaml` | 示例 case 1 |
| `evals/cases/stage-1/create-a-file.yaml` | 示例 case 2 |
| `evals/cases/stage-1/edit-in-place.yaml` | 示例 case 3 |
| `tests/test_stage_1_tools.py` | 工具单元测试 |
| `tests/test_stage_1_loop.py` | max_turns + 错误回传测试 |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/loop.py` | 加 `max_turns`、工具异常 → `is_error=True`、结果截断 |
| `src/my_agent/core/client.py` | 无需（工具列表由 registry 提供） |
| `src/my_agent/__main__.py` | 增加 `eval` 子命令 |
| `src/my_agent/__main__.py` | 在启动时注册新工具 |

### 关键 API

```python
# tools/bash.py
class BashInput(BaseModel):
    command: str
    timeout_s: int = Field(default=30, ge=1, le=600)

class BashTool(Tool[BashInput]):
    name = "bash"
    description = "Run a shell command in the project cwd. Stdout and stderr are merged."

    async def execute(self, input: BashInput) -> str:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", input.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), input.timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            raise ToolError(f"bash timed out after {input.timeout_s}s") from None
        return stdout_b.decode("utf-8", errors="replace")

# tools/edit_file.py
class EditFileInput(BaseModel):
    path: str
    old_string: str
    new_string: str

class EditFileTool(Tool[EditFileInput]):
    name = "edit_file"
    description = (
        "Replace the unique occurrence of old_string with new_string in the file. "
        "Fails if old_string appears zero or multiple times."
    )

    async def execute(self, input: EditFileInput) -> str:
        text = Path(input.path).read_text(encoding="utf-8")
        count = text.count(input.old_string)
        if count != 1:
            raise ToolError(f"edit_file: expected 1 match, found {count}")
        Path(input.path).write_text(
            text.replace(input.old_string, input.new_string), encoding="utf-8",
        )
        return f"OK: 1 replacement in {input.path}"

# core/loop.py（修改版）
class AgentLoop:
    MAX_TURNS: ClassVar[int] = 20
    MAX_TOOL_RESULT_CHARS: ClassVar[int] = 20_000

    async def run(self, user_input: str) -> str:
        history = MessageHistory()
        history.append_user(user_input)
        for turn in range(self.MAX_TURNS):
            response = await self.client.send(...)
            history.append_assistant(response.content)
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return _extract_text(response.content)
            for tu in tool_uses:
                try:
                    result = await self.registry.dispatch(tu.name, tu.input)
                except Exception as e:
                    history.append_tool_result(tu.id, _format_error(e), is_error=True)
                    continue
                history.append_tool_result(tu.id, _truncate(result, self.MAX_TOOL_RESULT_CHARS))
        raise LoopError(f"hit max_turns ({self.MAX_TURNS}) without final answer")
```

### Eval Harness 设计

#### YAML case schema

```yaml
name: read-and-translate
description: 读文件、翻译、写新文件
task: "读 README.md 的第一段，翻译成英文，写到 README.en.md"

# 可选：case 运行前的环境准备（agent 执行完后由 harness 清理）
setup:
  - write: {path: README.md, content: "# 示例\n\n这是一个测试文件。\n"}

# 规则断言（全部必须通过）
assertions:
  - tool_called: {name: read_file, input_contains: {path: "README.md"}}
  - tool_called: {name: write_file, input_contains: {path: "README.en.md"}}
  - file_exists: {path: README.en.md}
  - final_response_matches: "(?i)(done|finished|completed|translated)"

# 可选：LLM judge（多维度评分）
judge:
  rubric: |
    评价 agent 的最终翻译质量（0-10）：
    - 内容准确性
    - 英语地道程度
  threshold: 7

# 可选：超时（秒）
timeout: 60
```

#### Harness 架构

```python
# evals/harness.py
class CaseResult(BaseModel):
    name: str
    passed: bool
    assertion_results: list[AssertionResult]
    judge_score: float | None = None
    tokens: TokenUsage
    duration_s: float
    trace_path: Path

class Harness:
    def __init__(self, client: AnthropicClient, registry: ToolRegistry) -> None: ...

    async def run_case(self, case_path: Path, workdir: Path) -> CaseResult:
        """在 workdir（pytest tmp_path 风格）里执行 setup，跑 agent，收集 trace，执行断言。"""

    async def run_stage(self, stage: int) -> StageResult: ...
```

#### CLI 接入

```bash
uv run my-agent eval --stage 1              # 跑 stage 1 所有 case
uv run my-agent eval --case read-and-translate
uv run my-agent eval --stage 1 --judge      # 强制跑 LLM judge（默认按 case 配置）
uv run my-agent eval --stage 1 --format json > report.json
```

报告 txt 示例：

```
Stage 1 Evaluation — 2026-04-20 14:32:10
────────────────────────────────────────
✅ read-and-translate       (4/4 assertions, judge 8.2, 12.3s, 3.4k tokens)
✅ create-a-file            (3/3 assertions, 5.1s, 1.8k tokens)
❌ edit-in-place            (2/3 assertions — file_exists failed, 7.0s, 2.6k tokens)

Summary: 2/3 passed, 24.4s total, 7.8k tokens (~$0.04)
Trace dir: evals/reports/2026-04-20_143210/
```

## 验收标准

- [ ] 单元测试全绿：`uv run pytest tests/test_stage_1_*.py`
- [ ] mypy/ruff 全绿
- [ ] Demo：`uv run my-agent "读 README.md、写一段简短英文介绍到 README.en.md"` 成功
- [ ] `uv run my-agent eval --stage 1` 至少 3 个 case 全绿
- [ ] 工具抛异常（故意给 `read_file` 一个不存在路径）不 crash agent，模型能收到错误并尝试修正
- [ ] `max_turns` 保护：构造一个死循环场景（FakeLLM 持续返回 tool_use），确认 20 轮后抛 `LoopError`
- [ ] Bash 超时：`bash "sleep 60"` with `timeout_s=2` 在 2 秒内返回 timeout 错误

## Eval Cases

本 stage 至少提交 3 个 case（见 `evals/cases/stage-1/`）：

1. **`read-and-translate.yaml`** — 读 + 写 + 简单 LLM 任务
2. **`create-a-file.yaml`** — 纯写入，无前置文件
3. **`edit-in-place.yaml`** — 读 + 编辑 + 验证内容

Judge 可选启用，threshold ≥ 7。

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 1 段（1.1–1.12）**。

## 变更历史

- 2026-04-19：首版起草。
