---
name: my-agent-dev
description: Use when the user asks to continue developing, implement the next task, or work on the my_agent coding-agent project ("继续"、"下一步"、"开发 my-agent"). Reads SPEC.md progress table, finds next actionable sub-task, implements via TDD, updates progress table. Never skip steps.
---

# my-agent-dev workflow

驱动 `my_agent` 项目按 `SPEC.md` 详细任务表顺序推进。每次调用完成**一个**子任务的最小闭环（测试 + 实现 + 进度更新 + 汇报）。

## Step 1 — 定位位置

1. 读 `SPEC.md` 的**详细任务表**（Sub-task Level）。
2. 按优先级找下一个可做的子任务：
   a. 当前 Stage 有 🟡 sub-task → 继续那个
   b. 当前 Stage 有 🔴 且依赖列全 🟢 的 sub-task → 取编号最小的
   c. 当前 Stage 全部 🟢 → 跑该 Stage 的 `uv run my-agent eval --stage N`；全绿则 Stage 状态 → 🟢 并填完成日期，进入下一 Stage
   d. 所有 Stage 🟢 → 汇报"项目完成"
3. 汇报一句："即将开始 **N.M <任务名>**，预计涉及 `<files>`，测试入口 `<test path>`。确认开始？"
4. 等用户"go"/"继续"等明确回应再进入 Step 2。

## Step 2 — 读取设计

1. 打开 `specs/stage-N-*.md`，重点看：**目标 / 设计 / API / 验收标准 / Eval Cases**。
2. 如果 spec 与当前代码状态有明显冲突或信息缺失：**停下来**与用户对齐（改 spec 还是改实现），不要自作主张。

## Step 3 — TDD 实施

1. 先写/改 `tests/test_stage_N_*.py` 中对应测试用例。
2. `uv run pytest -k <test_name>` → 确认 **红**（测试先失败）。
3. 写最小实现让测试通过；避免超范围写代码。
4. **为每个新文件、类、函数添加详细注释**（见下方"注释规范"）。
5. `uv run pytest -k <test_name>` → **绿**。
6. 全绿后再跑：
   - `uv run ruff check`
   - `uv run ruff format --check`
   - `uv run mypy src`
   以上任意一项失败：修到全绿才能进入 Step 4。

## 注释规范（每个任务强制执行）

本项目**以学习 Python + agent 机制为首要目标**，注释是交付物的一部分，不是可选项。

**模块级 docstring**（每个 `.py` 文件顶部）：
```python
"""
<模块一句话功能描述>

架构角色：说明它在 agent 数据流中处于哪个位置，和哪些模块交互。
学习点：列出本文件涉及的关键 Python 或 agent 概念（如 Generic、asyncio、Pydantic 等）。
"""
```

**类级 docstring**：说明职责、生命周期、为什么这样设计（而不只是"是什么"）。

**函数/方法级 docstring**：说明入参含义、返回值含义、副作用；如果是 Anthropic 协议的一部分，描述协议步骤。

**内联注释**（`#`）：
- Python 语言机制：泛型 `Generic[T]`、`TypeVar`、`cast`、`ClassVar`、描述符、`@dataclass` 等，出现时都要注释解释。
- asyncio 模式：`await`、`CancelledError`、`create_subprocess_exec` 等，说明为什么要 async 及注意事项。
- Anthropic 协议步骤：`tool_use` 和 `tool_result` 的 id 对齐、消息历史格式、为什么顺序不能错。
- Pydantic 技巧：`Field(repr=False)`、`ConfigDict`、`model_json_schema()` 等，说明其作用。

**什么时候不需要注释**：
- 语义自明的单行代码（如 `return self._usage`）不需要重复描述行为。
- 标准库调用语义显而易见时（如 `Path(path).read_text()`）不必解释。

## Step 4 — 更新进度 & Git 提交

1. 打开 `SPEC.md`：
   - 该子任务状态 🔴/🟡 → **🟢**
   - 更新 Stage 级行的"进度"列（例 `3/9` → `4/9`）
2. 在对应 `specs/stage-N-*.md` 的"变更历史"追加一行：
   - `YYYY-MM-DD: 完成 N.M <任务名>`
3. **子任务 commit**（每个子任务完成后必做）：
   ```bash
   git add <涉及的文件>
   git commit -m "feat(stage-N): N.M <任务名简短描述>"
   ```
   - 只 stage 本次子任务涉及的文件，不要 `git add .`
   - commit message 格式：`feat(stage-N): N.M <任务名>` 例如 `feat(stage-0): 0.4 ToolRegistry`
4. 若该子任务是所在 Stage 的**最后一个 🔴**：
   - 跑 `uv run my-agent eval --stage N`
   - 全绿 → Stage 状态 → 🟢，填完成日期
   - 有红 → Stage 状态保持 🟡，并在任务表**新增条目**描述修复工作
5. **Stage PR**（整个 Stage 全部 🟢 + eval 全绿后）：
   - 从 `main` 新建分支 `stage-N` 并推送（如果还没建）
   - 实际上每个子任务 commit 都应在 `stage-N` 分支上完成
   - Stage 完成后对 `main` 发 PR，标题：`feat: Stage N — <Stage 名称>`

> **分支约定**：
> - `main`：只接受 Stage PR 合并，不直接 commit 代码
> - `stage-N`：该 Stage 所有子任务 commit 都在这里；Stage 完成后发 PR → `main`
> - 开始一个新 Stage 时，先 `git checkout -b stage-N`

## Step 5 — 汇报

一句话摘要：**完成任务号 + 关键文件变动 + 下一步建议任务号**。不需要长篇复盘。

## 禁止事项

- 跳过测试直接写实现
- 更新进度表但未真正验证（未跑测试/未全绿）
- 一次调用里做多个子任务（除非 spec 明确声明它们是同一最小闭环）
- 自行绕过或修改 spec；遇到 spec 需要变更时先告知用户并获批后更新 spec，再写代码
- 在 Stage 未完成前跑后一 Stage 的 eval
- 私自添加依赖；新增依赖前问用户

## 常用命令参考

```bash
# 装依赖
uv sync

# 跑单个 stage 测试
uv run pytest tests/test_stage_0_hello_loop.py -v

# 跑单个测试函数
uv run pytest -k test_hello_loop_one_turn

# lint + format + type
uv run ruff check
uv run ruff format
uv run mypy src

# eval
uv run my-agent eval --stage 1
```

## 状态图例（与 SPEC.md 同步）

- 🔴 not-started
- 🟡 in-progress
- 🟢 done
- 🟠 needs-update（spec 改了但代码未跟上）
