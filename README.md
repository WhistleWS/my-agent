# my-agent

A learning-oriented coding agent written in Python, built **stage by stage** to
systematically explore the mechanics of an LLM agent (tool loop, permissions,
context management, memory, subagents, hooks, skills).

Inspired by Anthropic's Claude Code; **not** a drop-in replacement.

## 状态

🚧 **骨架阶段** — spec 已就位，代码尚未开始。

所有开发由 `SPEC.md` 的**两级进度表**驱动。打开它，找当前 🟡 的子任务（或第一个 🔴 的阶段），就知道下一步做什么。

## 快速入口

- [SPEC.md](SPEC.md) — 愿景、架构、全局约束、**进度表**
- [specs/](specs/) — 8 个阶段的详细设计
- [.claude/skills/my-agent-dev/SKILL.md](.claude/skills/my-agent-dev/SKILL.md) — "一键开发"工作流

## 前置依赖

1. **[uv](https://docs.astral.sh/uv/)**：Python 包管理（`brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`）
2. **本地 CLIProxyAPI**（CPA）：Anthropic 兼容的本地代理，默认 `http://localhost:8317`
3. Python **3.12+**（`uv` 会自动安装）

## 首次设置

```bash
cp .env.example .env
# 编辑 .env，填 MY_AGENT_LLM_API_KEY（取自 CPA config.yaml 的 api-keys 列表）

uv sync              # 装依赖
uv run pytest        # 跑单元测试（Stage 0 起才有）
```

## 运行（Stage 0 起可用）

```bash
uv run my-agent "读 pyproject.toml 并告诉我它用的构建后端"
```

## 开发工作流

在 claude-code 里打开本项目，说"**继续开发 my-agent**"或"**做下一个任务**"。
`my-agent-dev` skill 会：
1. 扫 `SPEC.md` 进度表
2. 找下一个可做的子任务
3. 按 TDD 实施
4. 更新进度表
5. 汇报下一步

## License

Personal learning project. No license granted.
