# Stage 6 · Hooks

**状态**: 🔴 not-started
**依赖**: Stage 5
**预计规模**: ~400–500 行新增 / ~80 行修改

---

## 目标

把 agent 生命周期的关键位点暴露出来，让用户挂**外部 shell 命令**做响应。Hook 可以：

- **观察**（log、通知、metrics）
- **阻止**（返回 non-zero exit → 阻止该工具执行）
- **注入**（stdout 可以返回 JSON，给 agent 额外信息）

验收时：

```yaml
# .my-agent/hooks.yaml
pre_tool:
  - match: {tool: bash, input_contains: {command: "rm *"}}
    run: "echo 'blocked: rm commands require manual review'"
    exit_code_blocks: true

post_response:
  - run: "terminal-notifier -message 'agent done' -title my-agent"
```

```bash
uv run my-agent "..." # 最后自动弹桌面通知；rm 命令会被拦截
```

## 学习点

### Python

- **事件总线设计**：enum 位点 + 钩子注册表
- **外部进程的 JSON 协议**：stdout 是 JSON 就解析注入，否则只看 exit code
- **超时 + 隔离**：hook 别卡死主流程

### Agent

- Hook 和 permissions 的分工：permissions 管"能不能做"，hooks 管"做之前/之后顺便干点啥"
- 何时 hook 该用 async 逻辑而不是 shell（答案：几乎不需要，shell out 够用）

## 设计

### Hook 位点

| 位点 | 时机 | 可阻止 | 可注入 |
|---|---|---|---|
| `pre_tool` | 工具执行前（permission 检查之后） | ✅ | ❌ |
| `post_tool` | 工具执行后 | ❌ | ✅（拼到 tool_result） |
| `pre_response` | 每轮 LLM 调用前 | ❌ | ✅（作为 user 消息追加） |
| `post_response` | 每轮 LLM 调用后 | ❌ | ❌（纯通知） |
| `session_start` | 会话启动 | ❌ | ❌ |
| `session_end` | 会话结束 | ❌ | ❌ |

### 配置语法

```yaml
# .my-agent/hooks.yaml（项目级，不进 git）
# 或 ~/.my-agent/hooks.yaml（全局）

pre_tool:
  - match:
      tool: bash                        # 工具名（可选，缺省匹配全部）
      input_contains:                   # 字段 substring 匹配（可选）
        command: "rm *"
    run: "scripts/check-rm.sh"          # 相对路径 relative to cwd
    timeout: 5                          # 秒，默认 10
    exit_code_blocks: true              # true 时 non-zero 阻止工具

post_tool:
  - match: {tool: edit_file}
    run: "prettier --write {path}"      # {field_name} 占位符从 tool input 取

post_response:
  - run: "terminal-notifier -message 'done' -title my-agent"
    timeout: 2
```

### 关键 API

```python
# hooks/config.py
class HookMatch(BaseModel):
    tool: str | None = None
    input_contains: dict[str, str] = {}

class Hook(BaseModel):
    match: HookMatch = HookMatch()
    run: str
    timeout: int = 10
    exit_code_blocks: bool = False

class HooksConfig(BaseModel):
    pre_tool: list[Hook] = []
    post_tool: list[Hook] = []
    pre_response: list[Hook] = []
    post_response: list[Hook] = []
    session_start: list[Hook] = []
    session_end: list[Hook] = []

def load_hooks_config(cwd: Path) -> HooksConfig: ...

# hooks/runner.py
class HookResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

class HookRunner:
    def __init__(self, config: HooksConfig) -> None: ...

    async def run_pre_tool(
        self, tool_name: str, tool_input: dict
    ) -> HookRunResult:
        """运行所有匹配的 pre_tool hooks。如果有 exit_code_blocks 的 hook 返回非零，
        返回 blocked=True 并带 stderr 作为 reason。"""

    async def run_post_tool(
        self, tool_name: str, tool_input: dict, tool_output: str
    ) -> str:
        """返回增强后的 tool_output（如果 hook stdout 是 JSON {append: "..."} 则拼接）。"""

    async def run_simple(self, position: HookPosition) -> None:
        """session_start / session_end / post_response 用这个。忽略返回值，只记录日志。"""
```

### 数据流

```
tool_use → permission.check
          ├─ allowed → hook_runner.run_pre_tool
          │             ├─ blocked → append_tool_result(is_error=True, reason)
          │             └─ ok → tool.execute
          │                      └─ hook_runner.run_post_tool(enriches output)
          │                         → append_tool_result(enriched)
          └─ denied → append_tool_result(is_error=True, ...)
```

### 占位符替换

`run` 字符串里支持 `{field_name}`，从 `tool_input` 取值（shlex.quote 转义防注入）。未知字段报错。

## 验收标准

- [ ] Demo：`pre_tool` hook 拦截 `rm *` 命令，错误回传给模型
- [ ] Demo：`post_tool` hook 对 `edit_file` 自动跑 formatter
- [ ] Demo：`post_response` hook 发桌面通知（`terminal-notifier` 或 echo）
- [ ] Hook 超时不卡死：定 `sleep 10`、`timeout: 2`，2 秒后继续
- [ ] 占位符替换不产生 shell 注入（测试：`{path}` 是 `; rm -rf /` 时应 shlex-quoted）
- [ ] 单元测试覆盖所有位点和 `exit_code_blocks` 语义
- [ ] eval ≥ 2 个 case
- [ ] mypy/ruff 全绿

## Eval Cases

- `block-dangerous-rm.yaml` — hook 拦截 rm，agent 收到错误后放弃或换方式
- `auto-format-on-edit.yaml` — edit_file 后自动 formatter，断言文件内容被重排

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 6 段（6.1–6.6）**。

## 变更历史

- 2026-04-19：首版起草。
