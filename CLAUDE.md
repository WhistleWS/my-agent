# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Workflow

**Always start by reading `SPEC.md`** — it is the SSOT. Find the first 🟡 (in-progress) or 🔴 (not-started with all dependencies 🟢) sub-task, then read the corresponding `specs/stage-N-*.md` before touching any code.

Use the `my-agent-dev` skill (`.claude/skills/my-agent-dev/SKILL.md`) to drive development: it handles the full TDD cycle and progress table updates.

## Commands

```bash
uv sync                              # install / refresh dependencies
uv run my-agent "..."                # run the agent (Stage 0+)
uv run pytest                        # all unit tests
uv run pytest tests/test_stage_0_hello_loop.py -v   # single stage
uv run pytest -k test_name           # single test
uv run my-agent eval --stage 1       # end-to-end eval (requires live CPA)
uv run ruff check && uv run ruff format --check
uv run mypy src
```

A sub-task is only **done** when pytest, ruff, and mypy are all green.

## Architecture

The agent is built in 8 sequential stages. Each stage adds one capability layer; earlier stages must be complete before starting the next.

**Core data flow** (Stage 0 baseline):

```
__main__.py → AgentLoop (core/loop.py)
                ├─ AnthropicClient (core/client.py)   ← only place that imports anthropic
                ├─ MessageHistory (core/history.py)
                └─ ToolRegistry (tools/registry.py)
                     └─ Tool[InputModel] subclasses (tools/*.py)
```

**Layers added per stage:**

| Stage | What it adds | Key files |
|-------|-------------|-----------|
| 2 | Permission gate before every tool call | `security/permissions.py` |
| 3 | Streaming events + rich TUI | `core/client.py`, `ui/renderer.py` |
| 4 | AGENT.md injection, memory, compaction, sessions | `context/`, `memory/`, `core/session.py`, `core/prompt.py` |
| 5 | Sub-agent dispatch with isolated context | `agents/subagent.py`, `agents/dispatcher.py` |
| 6 | Shell hooks at lifecycle positions | `hooks/runner.py`, `hooks/config.py` |
| 7 | Skill files loaded as prompt fragments | `skills/loader.py`, `tools/invoke_skill.py` |

## Key Constraints

- **`core/client.py` is the only file that may `import anthropic`.**
- All tool inputs are Pydantic `BaseModel`; `to_anthropic_schema()` generates the JSON schema automatically — never write it by hand.
- Every `async` entry point must handle `asyncio.CancelledError`.
- `structlog` only; no `print()` in runtime code.
- All config (base URL, API key, model) lives in `config.py` via `python-dotenv`; other modules import from `config`.

## LLM Endpoint

Defaults to a local CLIProxyAPI at `http://localhost:8317` (Anthropic-compatible).

```bash
# .env
MY_AGENT_LLM_BASE_URL=http://localhost:8317
MY_AGENT_LLM_API_KEY=sk-...
MY_AGENT_MODEL=claude-sonnet-4-5-20250929   # optional override
```

To switch to the official Anthropic API, change only these two env vars — no code changes needed.

## Testing Strategy

- **Unit tests** (`tests/`): use `FakeLLM` fixture from `tests/conftest.py` — pre-recorded scripted turns, zero API calls.
- **Evals** (`evals/cases/stage-N/*.yaml`): hit the real CPA; run manually with `uv run my-agent eval --stage N`; ≥2 cases required per stage; a stage is not 🟢 until its evals pass.

## Progress Tracking

`SPEC.md` § 7 (detailed task table) is the canonical checklist. Update it after every sub-task:

- Starting: 🔴 → 🟡
- Done (tests + mypy + ruff green): 🟡 → 🟢
- Spec changed but code not yet updated: → 🟠

After every code change, append a line to the relevant `specs/stage-N-*.md` "变更历史" section.

---

## General LLM Coding Guidelines (from andrej-karpathy-skills)

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

## Learning-Oriented Comments

**This project is a learning exercise — every non-trivial piece of code must be explained in comments.**

For each file / function / class written:
- Add a module-level docstring explaining *what* this module does and *why* it exists in the agent architecture.
- Add a docstring to every class explaining its role and how it fits into the larger data flow.
- Add inline comments for any Python mechanic that a learner might not immediately understand: generics, `cast`, `asyncio` patterns, Pydantic field tricks, descriptor protocol, etc.
- For Anthropic API interactions, comment the protocol steps (e.g., "assistant returns tool_use → we must echo it back before appending tool_result").
- Comments should explain the *why* and the *how*, not just restate the code.
