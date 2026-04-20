# Stage 2 · Permissions

**状态**: 🔴 not-started
**依赖**: Stage 1
**预计规模**: ~400–500 行新增 / ~50 行修改

---

## 目标

在工具执行前增加**权限层**：默认 `ask` 模式每次工具调用都询问；支持 CLI 参数和规则文件预授权；支持会话级缓存；支持 `deny` 规则硬阻断。

这是安全关键的一阶段 —— agent 能执行 bash 意味着没有权限层就是一把枪。

验收时：

```bash
# 默认：每次 bash 都问
uv run my-agent "列出当前目录的 python 文件"

# 预授权：整场会话 read_file 不再问
uv run my-agent --allow 'read_file:*' "读 pyproject.toml"

# 硬阻断：bash 永不允许（即使 ask 模式下用户输入 yes 也拒绝）
uv run my-agent --deny 'bash:*' "..."
```

## 学习点

### Python

- **`Protocol` 类型**：结构化 duck typing
- **装饰器**（中间件模式）：`Callable` 包装
- **交互式输入**：`asyncio` 场景下的 `input()` 替代（`loop.run_in_executor` 或 `aioconsole`）
- **`fnmatch`** 和 glob 匹配

### Agent

- 权限模型的三个维度：**模式**（auto/ask/deny-by-default）、**规则**（allow/deny）、**会话缓存**
- 为什么工具执行前而非后检查（防止已执行 `rm -rf` 才问）
- 拒绝后的反馈回传 —— 以 `tool_result is_error=True` 让模型看到"这次拒绝了"

## 设计

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/my_agent/security/__init__.py` | 占位 |
| `src/my_agent/security/permissions.py` | 权限核心：`PermissionContext`, `Rule`, `PermissionDecision` |
| `src/my_agent/security/prompt.py` | 终端交互式确认（async-safe） |
| `tests/test_stage_2_permissions.py` | 单元测试（模式 × 规则 × 缓存矩阵） |
| `evals/cases/stage-2/*.yaml` | eval：允许、拒绝、预授权三种情形 |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/loop.py` | 在 `dispatch` 前插入 `await permission_ctx.check(tool_name, input)` |
| `src/my_agent/__main__.py` | 新增 CLI 参数：`--allow`、`--deny`、`--permission-mode` |

### 关键 API

```python
# security/permissions.py
class PermissionMode(StrEnum):
    AUTO = "auto"              # 全允许（仅开发用）
    ASK = "ask"                # 默认：每次问，除非被 allow 命中
    DENY_BY_DEFAULT = "deny"   # 不在 allow 里的一律拒绝

class RuleAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"

class Rule(BaseModel):
    tool_name_glob: str        # 例：read_file、*_file、bash
    input_field_globs: dict[str, str] = {}  # 例：{"path": "src/**"}
    action: RuleAction

    def matches(self, tool_name: str, tool_input: dict) -> bool: ...

@dataclass
class PermissionDecision:
    allowed: bool
    reason: str                # 用于日志 & 错误回传

class PermissionContext:
    def __init__(
        self,
        mode: PermissionMode,
        rules: list[Rule],
        prompter: Prompter | None = None,  # Protocol
    ) -> None: ...

    async def check(self, tool_name: str, tool_input: dict) -> PermissionDecision:
        """
        顺序：
          1. deny 规则命中 → 拒绝（不询问）
          2. allow 规则命中 → 允许
          3. 模式 auto → 允许
          4. 模式 ask → 调用 prompter.ask(...)；结果可加入会话级缓存
          5. 模式 deny → 拒绝
        """

# security/prompt.py
class Prompter(Protocol):
    async def ask(self, tool_name: str, tool_input: dict) -> AskResult: ...

class AskResult(StrEnum):
    ONCE = "once"                # 本次允许
    REMEMBER_TOOL = "remember"   # 整场会话该 tool 不再问
    REMEMBER_EXACT = "exact"     # 同样参数不再问
    NO = "no"

class TerminalPrompter(Prompter):
    async def ask(self, tool_name: str, tool_input: dict) -> AskResult:
        # 用 rich.Prompt.ask，run_in_executor 包装
        ...
```

### CLI 规则语法

```bash
--allow 'read_file:*'              # 所有 read_file 允许
--allow 'edit_file:path=src/**'    # 只允许改 src/ 下文件
--allow 'bash:command=ls *'        # 只允许 ls 开头的 bash
--deny  'bash:command=rm *'        # rm 永不允许（优先级高于 allow）
--permission-mode auto             # 开发用；覆盖 ask
```

### 会话级缓存

`TerminalPrompter` 用户选 "REMEMBER_TOOL" / "REMEMBER_EXACT" 时，`PermissionContext` 动态生成一条等价的 allow `Rule`，后续 check 直接命中规则路径（不重新询问）。

### 数据流（新增 check 步骤）

```
loop.dispatch(tool_use)
  ├─ permission_ctx.check(name, input)
  │    ├─ hit deny → return denied
  │    ├─ hit allow → return allowed
  │    ├─ mode auto → return allowed
  │    ├─ mode ask → prompter.ask() → (once/remember/no)
  │    └─ mode deny → return denied
  ├─ if denied: append_tool_result(id, reason, is_error=True)
  └─ else: tool.execute(input) → append_tool_result(id, result)
```

## 验收标准

- [ ] 单元测试覆盖：模式 × 规则（allow/deny）× 缓存 矩阵
- [ ] 默认 `ask` 模式下，`bash` 调用会停下来询问
- [ ] `--allow 'bash:*'` 后同样调用直接放行
- [ ] `--deny 'bash:rm *'` 硬阻断，即便 `--allow 'bash:*'` 也拦截（deny 优先）
- [ ] 拒绝时模型通过 `tool_result is_error=True` 看到原因，可以 retry 或切换工具
- [ ] eval 有 3 个 case 覆盖：允许放行、预授权、硬阻断
- [ ] mypy/ruff 全绿

## Eval Cases

- `allow-pre-authorized.yaml` — `--allow 'read_file:*'`，agent 不应触发任何确认
- `deny-bash-rm.yaml` — agent 试图 `rm -rf` 被拦，然后模型放弃或换工具
- `ask-then-allow.yaml` — 默认 ask 模式下 prompter 返回 `REMEMBER_TOOL`，第二次调用不再问

FakeLLM 测试负责模式矩阵；eval 负责真实交互行为。

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 2 段（2.1–2.6）**。

## 变更历史

- 2026-04-19：首版起草。
