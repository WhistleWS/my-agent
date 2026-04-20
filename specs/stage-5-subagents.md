# Stage 5 · Subagents

**状态**: 🔴 not-started
**依赖**: Stage 4
**预计规模**: ~400–500 行新增 / ~100 行修改

---

## 目标

让主 agent 能派发**独立 context 的子 agent**执行子任务，只把摘要拿回来。这是真正体现 agent 复合能力的机制：主 agent 不需要把海量中间产物（文件列表、搜索结果、探索笔记）塞进自己的 context 就能完成复杂任务。

验收时：

```bash
uv run my-agent "探查这个仓库，告诉我主循环在哪里"
# 主 agent 派一个 subagent 去读文件、grep、glob
# subagent 返回："主循环在 src/my_agent/core/loop.py:L42，负责 ..."
# 主 agent 只看到这段结论，不会被中间的 tool_use/tool_result 淹没
```

## 学习点

### Python

- **递归结构的类型**：主 `AgentLoop` 内调用子 `SubAgentLoop` 是 OK 的递归（有 max_turns 限制）
- **接口抽取**：从 `AgentLoop` 中抽出公共代码（`core/loop_base.py`）供 subagent 复用
- **资源隔离**：每个 subagent 独立的 `MessageHistory`、独立的 `Registry`（白名单工具）

### Agent

- **Context isolation** 的价值：子 agent 的 50 轮探索 → 主 agent 的 1 行结论
- **工具白名单**：不是所有工具都该给 subagent（比如 subagent 不该再能 `dispatch_agent`，防止递归爆炸）
- **摘要返回**：subagent 的最后一条 assistant 文本就是 summary；或者显式用 `return_summary` 工具结束

## 设计

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/my_agent/agents/__init__.py` | 占位 |
| `src/my_agent/agents/subagent.py` | `SubAgentLoop`，基本上是 `AgentLoop` 的受限版 |
| `src/my_agent/agents/dispatcher.py` | `DispatchAgentTool`：主 agent 可用的工具 |
| `tests/test_stage_5_subagents.py` | 覆盖派发 + 白名单 + max_turns 保护 |
| `evals/cases/stage-5/*.yaml` | 验证主 agent 合理使用 subagent |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/loop.py` | 抽出 `BaseLoop` 公共父类 |

### 关键 API

```python
# agents/subagent.py
class SubAgentConfig(BaseModel):
    name: str
    description: str
    tool_whitelist: list[str]       # 例：["read_file", "glob", "grep"]
    max_turns: int = 10

class SubAgentLoop(BaseLoop):
    """基本同 AgentLoop，但：工具白名单裁剪、独立 history、无 dispatch_agent 工具。"""

    async def run(self, task: str) -> SubAgentResult: ...

class SubAgentResult(BaseModel):
    summary: str                    # 最终 assistant 文本
    turns_used: int
    tokens: TokenUsage
    hit_max_turns: bool

# agents/dispatcher.py
class DispatchAgentInput(BaseModel):
    agent: str                      # subagent 配置的 name（如 "explorer"）
    task: str                       # 自然语言任务

class DispatchAgentTool(Tool[DispatchAgentInput]):
    name = "dispatch_agent"
    description = (
        "Dispatch a subagent to work on an isolated task. The subagent has its own "
        "context and tool set. You'll receive only its final summary, not the "
        "intermediate tool calls. Good for: repo exploration, parallel searches, "
        "anything that would produce lots of intermediate results."
    )

    def __init__(self, registry: ToolRegistry, client: AnthropicClient,
                 configs: dict[str, SubAgentConfig]) -> None: ...

    async def execute(self, input: DispatchAgentInput) -> str:
        cfg = self._configs[input.agent]
        sub = SubAgentLoop(cfg, self._client, self._registry.restricted_to(cfg.tool_whitelist))
        result = await sub.run(input.task)
        return self._format(result)
```

### 默认配置（打包一个）

```python
DEFAULT_SUBAGENTS = {
    "explorer": SubAgentConfig(
        name="explorer",
        description="Read-only codebase exploration (glob, grep, read_file).",
        tool_whitelist=["glob", "grep", "read_file"],
        max_turns=20,
    ),
}
```

## 验收标准

- [ ] 主 agent 派发 explorer，它只能用白名单里的工具；尝试 bash 时失败（工具不存在）
- [ ] Subagent 独立 history：主 agent 的 `history` 里只见 `dispatch_agent` 的 tool_use 和 summary，不见 subagent 的中间消息
- [ ] Subagent 命中 max_turns 时返回结果标记 `hit_max_turns=True`，主 agent 能看到
- [ ] eval case 验证主 agent 会在合适时机调用 subagent（而不是自己硬扫 repo）
- [ ] mypy/ruff 全绿

## Eval Cases

- `explore-repo.yaml` — "告诉我主循环在哪里"，断言 `tool_called: {name: dispatch_agent, times: ">=1"}`
- `subagent-respects-whitelist.yaml` — 明确指示 explorer 用 bash，然后断言 bash 未被调用、有恰当错误

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 5 段（5.1–5.5）**。

## 变更历史

- 2026-04-19：首版起草。
