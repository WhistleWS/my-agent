# my-agent — Specification

> **SSOT warning.** 本文件是 my-agent 项目的唯一真相源。所有代码改动必须先体现在本文件或对应的 `specs/stage-N-*.md` 中。
> 开新会话第一件事：读下面的**进度表**，定位当前工作位置。

---

## 1. 愿景

用 Python 从零构建一个 Mini claude-code 级别的 CLI 编码 agent。它不追求功能完备，追求的是让作者**系统性地理解 agent 的每一种核心机制**，并在此过程中扎实地学习 Python（Pydantic + async 是主要学习目标）。

## 2. 设计原则

1. **Async-first** — 所有 I/O（LLM、文件、subprocess）全部走 `async`。
2. **Pydantic-first** — 所有结构化数据（工具输入、配置、消息、session）用 `BaseModel`。
3. **Stage-isolation** — 每个阶段都能独立 demo、独立验收；不做跨阶段泄漏的快捷方式。
4. **Spec-driven** — 代码永远跟 spec 走，不反过来。漂移就先让 spec 变（或把任务标 🟠）。
5. **No premature abstraction** — 不给未来需求预留抽象层；YAGNI 贯穿始终。

## 3. 全局架构

```
┌──────────────────────────────────────────────────┐
│  CLI (my_agent/__main__.py)                      │
│  ├── 解析参数、加载配置 (.env)                     │
│  └── 启动 AgentLoop                               │
└──────────────────────┬───────────────────────────┘
                       │
           ┌───────────▼─────────────┐
           │   AgentLoop (core/loop) │◄──── MessageHistory
           │   ├─ 组装 system prompt │◄──── Context 注入 (Stage 4)
           │   ├─ 调用 LLM           │◄──── AnthropicClient
           │   ├─ 解析 tool_use      │
           │   ├─ 执行工具 (并发/串行) │◄──── ToolRegistry + PermissionLayer
           │   └─ 回传 tool_result   │
           └───────────┬─────────────┘
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
Tools            Permissions         Memory/Context
(tools/*.py)    (security/)         (memory/, context/)
```

## 4. 全局约束

以下规则所有阶段都必须遵守，违反时在 PR/commit message 说明原因：

1. **LLM 端点**：统一走 `src/my_agent/core/client.py`。其他模块不得直接 `import anthropic`。base URL / API key / model 只从环境变量读（`MY_AGENT_LLM_BASE_URL` / `MY_AGENT_LLM_API_KEY` / `MY_AGENT_MODEL`）。
2. **工具**：继承 `src/my_agent/tools/base.py:Tool[InputModel]`，输入用 Pydantic `BaseModel`，不能手写 JSON schema。
3. **async**：每个 `async` 入口都要能响应 `asyncio.CancelledError`。长时间 blocking 的系统调用用 `asyncio.create_subprocess_exec` 或 `run_in_executor`。
4. **日志**：用 `structlog`。禁止在运行时代码里写 `print()`（测试 fixture 除外）。
5. **配置**：`.env` 通过 `python-dotenv` 读，只在 `config.py` 一处；其他模块从 `config` 拿值。
6. **秘密**：永远不提交 `.env`，不 log api_key。
7. **类型**：`mypy --strict` 全绿，才算一个子任务 done。
8. **格式**：`ruff format` + `ruff check` 全绿。
9. **注释（学习优先）**：本项目以学习为主，每个文件、类、函数都必须有说明性注释或 docstring，解释"为什么这样设计"和"它在 agent 架构里扮演什么角色"。涉及 Python 语言机制（泛型、`cast`、`asyncio`、Pydantic 字段、描述符等）的地方加内联注释，帮助读者理解。Anthropic 协议的每个关键步骤（`tool_use` → `tool_result` 对齐、历史格式等）必须有协议说明注释。

## 5. LLM 端点集成（CLIProxyAPI）

本项目默认对接作者本地的 **CLIProxyAPI**（CPA），运行在 `http://localhost:8317`，提供 Anthropic 兼容的 `/v1/messages`。`AsyncAnthropic(base_url=..., api_key=...)` 即可连上，无需任何自定义 HTTP 代码。

- **启动健康检查**：`config.py` 在进程启动时 `GET {base_url}/v1/models`，失败则给出可执行的错误提示（"CPA 未启动？请 `/Users/whistle/Desktop/CLIProxyAPI_6.9.26_darwin_arm64/cli-proxy-api` 运行"）。
- **模型名**：默认 `claude-sonnet-4-5-20250929`。真实可用清单见 `http://localhost:8317/management.html`。
- **切换到官方 API**：只改 `MY_AGENT_LLM_BASE_URL=https://api.anthropic.com` 和 `MY_AGENT_LLM_API_KEY`，代码零改动。
- **行为差异记录**：CPA 与官方 API 在 `usage` 字段、streaming 事件序列上可能略有差异。任何在开发中发现的差异必须记录到 `specs/stage-N-*.md` 的"变更历史"或 `SPEC.md` 的本节附录。

---

## 6. 阶段总览（Stage Level）

| # | 阶段 | 状态 | 进度 | 开始 | 完成 | Spec |
|---|------|------|------|------|------|------|
| 0 | Hello Loop | 🟡 | 6/9 | 2026-04-19 | - | [stage-0](specs/stage-0-hello-loop.md) |
| 1 | Core Tools + Eval Harness | 🔴 | 0/12 | - | - | [stage-1](specs/stage-1-core-tools.md) |
| 2 | Permissions | 🔴 | 0/6 | - | - | [stage-2](specs/stage-2-permissions.md) |
| 3 | Streaming & TUI | 🔴 | 0/7 | - | - | [stage-3](specs/stage-3-streaming-tui.md) |
| 4 | Context & Memory | 🔴 | 0/11 | - | - | [stage-4](specs/stage-4-context-memory.md) |
| 5 | Subagents | 🔴 | 0/5 | - | - | [stage-5](specs/stage-5-subagents.md) |
| 6 | Hooks | 🔴 | 0/6 | - | - | [stage-6](specs/stage-6-hooks.md) |
| 7 | Skills | 🔴 | 0/7 | - | - | [stage-7](specs/stage-7-skills.md) |

**状态图例**：🔴 not-started · 🟡 in-progress · 🟢 done · 🟠 needs-update（spec 已改但代码未跟上）

---

## 7. 详细任务表（Sub-task Level）

这是**真正驱动开发的唯一清单**。`my-agent-dev` skill 每次从这里查下一个可做任务（状态 🔴 且依赖全 🟢，或状态 🟡）。

### Stage 0 · Hello Loop

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 0.1 | AsyncAnthropic 薄封装（读 `MY_AGENT_LLM_*` env，启动健康检查）+ token 计数 | `src/my_agent/core/client.py` | 🟢 | - |
| 0.2 | MessageHistory（Anthropic 消息格式） | `src/my_agent/core/history.py` | 🟢 | - |
| 0.3 | `Tool[Input]` 基类 + Pydantic 泛型 + `to_anthropic_schema()` | `src/my_agent/tools/base.py` | 🟢 | - |
| 0.4 | ToolRegistry（注册 + dispatch） | `src/my_agent/tools/registry.py` | 🟢 | 0.3 |
| 0.5 | ReadFileTool | `src/my_agent/tools/read_file.py` | 🟢 | 0.3 |
| 0.6 | AgentLoop（最小主循环） | `src/my_agent/core/loop.py` | 🟢 | 0.1, 0.2, 0.4 |
| 0.7 | CLI 入口 + config 加载 | `src/my_agent/__main__.py`, `config.py` | 🔴 | 0.6 |
| 0.8 | FakeLLM fixture（scripted turns） | `tests/conftest.py` | 🟢 | 0.1 |
| 0.9 | Stage 0 单元测试（一轮 tool_use 往返） | `tests/test_stage_0_hello_loop.py` | 🟢 | 0.5, 0.6, 0.8 |

### Stage 1 · Core Tools + Eval Harness

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 1.1 | WriteFileTool | `src/my_agent/tools/write_file.py` | 🔴 | 0.3 |
| 1.2 | EditFileTool（精确字符串替换） | `src/my_agent/tools/edit_file.py` | 🔴 | 0.3 |
| 1.3 | BashTool（async subprocess + 超时 + 截断） | `src/my_agent/tools/bash.py` | 🔴 | 0.3 |
| 1.4 | GlobTool | `src/my_agent/tools/glob.py` | 🔴 | 0.3 |
| 1.5 | GrepTool（基于 ripgrep 或纯 Python） | `src/my_agent/tools/grep.py` | 🔴 | 0.3 |
| 1.6 | `max_turns` 循环保护 + 结果截断 | `src/my_agent/core/loop.py` | 🔴 | 0.6 |
| 1.7 | 工具异常包装成 tool_result 返回 | `src/my_agent/core/loop.py` | 🔴 | 0.6 |
| 1.8 | Eval harness（yaml case + 规则断言） | `evals/harness.py` | 🔴 | 0.6 |
| 1.9 | LLM-as-judge（可选维度） | `evals/judge.py` | 🔴 | 1.8 |
| 1.10 | Eval CLI 子命令 `my-agent eval` | `src/my_agent/__main__.py` | 🔴 | 1.8 |
| 1.11 | Stage 1 单元测试 | `tests/test_stage_1_*.py` | 🔴 | 1.1–1.7 |
| 1.12 | Stage 1 eval cases（≥3 个） | `evals/cases/stage-1/*.yaml` | 🔴 | 1.8, 1.11 |

### Stage 2 · Permissions

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 2.1 | `PermissionContext` + auto/ask/deny 模式 | `src/my_agent/security/permissions.py` | 🔴 | - |
| 2.2 | 规则匹配（工具名 + 字段 glob） | `src/my_agent/security/permissions.py` | 🔴 | 2.1 |
| 2.3 | 交互式确认 prompt | `src/my_agent/security/permissions.py` | 🔴 | 2.1 |
| 2.4 | 权限中间件接入主循环 | `src/my_agent/core/loop.py` | 🔴 | 2.1, 1.6 |
| 2.5 | Stage 2 单元测试 | `tests/test_stage_2_permissions.py` | 🔴 | 2.4 |
| 2.6 | Stage 2 eval cases | `evals/cases/stage-2/*.yaml` | 🔴 | 2.4 |

### Stage 3 · Streaming & TUI

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 3.1 | 客户端切流式（`messages.stream`） | `src/my_agent/core/client.py` | 🔴 | 0.1 |
| 3.2 | 流式事件 → 主循环 tool_use 抽取 | `src/my_agent/core/loop.py` | 🔴 | 3.1 |
| 3.3 | `rich` 渲染（工具框、diff、spinner） | `src/my_agent/ui/renderer.py` | 🔴 | - |
| 3.4 | Ctrl+C 优雅中断 + 历史保留 | `src/my_agent/core/loop.py` | 🔴 | 3.2 |
| 3.5 | `CancelledError` 传播到所有工具 | `src/my_agent/tools/bash.py` 等 | 🔴 | 3.4 |
| 3.6 | Stage 3 单元测试 | `tests/test_stage_3_*.py` | 🔴 | 3.4 |
| 3.7 | Stage 3 eval cases | `evals/cases/stage-3/*.yaml` | 🔴 | 3.4 |

### Stage 4 · Context & Memory

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 4.1 | `AGENT.md` 自动发现 + 注入 | `src/my_agent/context/project.py` | 🔴 | - |
| 4.2 | Memory 文件扫描（frontmatter） | `src/my_agent/memory/store.py` | 🔴 | - |
| 4.3 | Memory 相关性排序 | `src/my_agent/memory/ranking.py` | 🔴 | 4.2 |
| 4.4 | `save_memory` 工具 | `src/my_agent/tools/save_memory.py` | 🔴 | 4.2 |
| 4.5 | Token 计数 + 压缩触发阈值 | `src/my_agent/context/compaction.py` | 🔴 | 0.1 |
| 4.6 | 摘要压缩（保留最近 N 轮 + 摘要） | `src/my_agent/context/compaction.py` | 🔴 | 4.5 |
| 4.7 | Session 序列化到 JSONL | `src/my_agent/core/session.py` | 🔴 | 0.2 |
| 4.8 | `--resume <id>` CLI 选项 | `src/my_agent/__main__.py` | 🔴 | 4.7 |
| 4.9 | 系统 prompt 拼装重构（分片 + 缓存） | `src/my_agent/core/prompt.py` | 🔴 | 4.1, 4.3 |
| 4.10 | Stage 4 单元测试 | `tests/test_stage_4_*.py` | 🔴 | 4.1–4.8 |
| 4.11 | Stage 4 eval cases（含跨会话记忆） | `evals/cases/stage-4/*.yaml` | 🔴 | 4.8 |

### Stage 5 · Subagents

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 5.1 | `SubAgentLoop`（独立 history） | `src/my_agent/agents/subagent.py` | 🔴 | 0.6 |
| 5.2 | `dispatch_agent` 工具 | `src/my_agent/agents/dispatcher.py` | 🔴 | 5.1, 0.3 |
| 5.3 | 子 agent 工具白名单配置 | `src/my_agent/agents/dispatcher.py` | 🔴 | 5.2 |
| 5.4 | Stage 5 单元测试 | `tests/test_stage_5_subagents.py` | 🔴 | 5.2 |
| 5.5 | Stage 5 eval cases | `evals/cases/stage-5/*.yaml` | 🔴 | 5.2 |

### Stage 6 · Hooks

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 6.1 | `HookRunner`（pre/post tool, pre/post response） | `src/my_agent/hooks/runner.py` | 🔴 | - |
| 6.2 | `.my-agent/hooks.yaml` 配置加载 | `src/my_agent/hooks/config.py` | 🔴 | - |
| 6.3 | Hook 阻塞返回（可阻止工具执行） | `src/my_agent/hooks/runner.py` | 🔴 | 6.1 |
| 6.4 | 超时保护 + 错误处理 | `src/my_agent/hooks/runner.py` | 🔴 | 6.1 |
| 6.5 | Stage 6 单元测试 | `tests/test_stage_6_hooks.py` | 🔴 | 6.3 |
| 6.6 | Stage 6 eval cases | `evals/cases/stage-6/*.yaml` | 🔴 | 6.3 |

### Stage 7 · Skills

| # | 任务 | 文件 | 状态 | 依赖 |
|---|------|------|------|------|
| 7.1 | Skill 文件扫描 + frontmatter 解析 | `src/my_agent/skills/loader.py` | 🔴 | - |
| 7.2 | Skill 列表注入 system prompt | `src/my_agent/core/prompt.py` | 🔴 | 7.1, 4.9 |
| 7.3 | `invoke_skill` 工具（注入 body） | `src/my_agent/tools/invoke_skill.py` | 🔴 | 7.1 |
| 7.4 | CLI 前缀触发：`/skill-name ...` | `src/my_agent/__main__.py` | 🔴 | 7.1 |
| 7.5 | 示例 skill（至少 1 个 markdown 文件） | `~/.my-agent/skills/*.md` | 🔴 | 7.3 |
| 7.6 | Stage 7 单元测试 | `tests/test_stage_7_skills.py` | 🔴 | 7.4 |
| 7.7 | Stage 7 eval cases | `evals/cases/stage-7/*.yaml` | 🔴 | 7.4 |

---

## 8. Progress Table 维护规则

1. 开始某子任务：状态 🔴 → 🟡
2. 子任务测试通过且 mypy/ruff 全绿：🟡 → 🟢
3. 一个 Stage 全部 🟢 + eval 全绿：Stage 状态 → 🟢，填完成日期
4. 修改某阶段 spec 后代码需同步：相关子任务 → 🟠，处理完再 🟢
5. 开新会话第一件事：调用 `my-agent-dev` skill，由它扫表并汇报下一步

## 9. 跨阶段约定（速查）

| 主题 | 约定 |
|------|------|
| LLM 出口 | `core/client.py` 唯一封装 |
| 测试 | pytest + `FakeLLM`（`tests/conftest.py`），零 token |
| Eval | `evals/cases/stage-N/*.yaml` + `evals/harness.py`；每阶段 ≥ 2 个 case；Stage 🟢 要求 eval 全绿；手动跑，不入 CI |
| 日志 | `structlog` JSON；`MY_AGENT_LOG_LEVEL` / `MY_AGENT_DEBUG` 控制 |
| 配置 | 只在 `config.py` 读 `.env` |
| 成本追踪 | `core/client.py` 累计 token；session 结束打印 |
| 类型 | `mypy --strict` |
| 格式 | `ruff format` + `ruff check` |
| 文档 | 代码改动前先改 spec；改动完在对应 stage spec 的"变更历史"追加一行 |

## 10. Out of Scope

- MCP 集成（可能的未来 Stage 8+）
- Plugin 系统
- Windows 支持
- Web / GUI
- 多 LLM 抽象（坚持 Anthropic 格式）

## 11. 变更历史

- 2026-04-19：首版 spec 起草（`plan` 批准后落地）。
