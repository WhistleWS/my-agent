# Stage 7 · Skills

**状态**: 🔴 not-started
**依赖**: Stage 4（system prompt builder）、Stage 6（可选：skill 触发的 hook）
**预计规模**: ~400–500 行新增 / ~80 行修改

---

## 目标

Skill = **一段可复用的 prompt 片段 + 元数据**，让用户把"写好的领域专长"装进 agent：

- 扫描 `~/.my-agent/skills/*.md`（全局）+ `.my-agent/skills/*.md`（项目）
- 每个 skill 是一份 frontmatter markdown 文件（和 Claude Code 的 skill 同构）
- **启动时只注入 name + description 到 system prompt**（省 token），body 按需加载
- 模型通过 `invoke_skill` 工具把 body 调进当前轮次
- CLI 也支持 `/skill-name <args>` 显式触发

验收时：

```bash
# ~/.my-agent/skills/translate-en.md
# ---
# name: translate-en
# description: Translate the given text into English, keeping code and identifiers intact.
# ---
# Rules:
# 1. Never translate variable names.
# 2. ...

uv run my-agent "/translate-en 把 README 里的第一段翻译"
# → agent 直接拿到 skill body 当 user prefix，输出英文翻译
```

## 学习点

### Python

- **Frontmatter 解析**（复用 Stage 4b 的模式）
- **Glob 扫描 + 合并**（全局 + 项目级，项目优先）
- **动态扩展点设计**：API 应允许用户只加一个 markdown 文件就扩展 agent 能力

### Agent

- **Skill 与 Memory 的区别**：Memory 是事实/偏好（自动挑选注入），Skill 是动作/流程（按需调用）
- **为什么启动时不全文注入**：skill 可能十几个、每个几百行 → 会吃光 context；所以只放 index
- **Skill 与 Subagent 的区别**：skill 是 prompt 模板（在主 agent context 内执行），subagent 是独立 context 的子进程

## 设计

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/my_agent/skills/__init__.py` | 占位 |
| `src/my_agent/skills/loader.py` | 扫描 skill 文件、解析 frontmatter、缓存 |
| `src/my_agent/tools/invoke_skill.py` | `invoke_skill` 工具：按 name 取 body 返回 |
| `tests/test_stage_7_skills.py` | 加载、冲突解决、invoke 测试 |
| `evals/cases/stage-7/*.yaml` | 真实 skill 调用 eval |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/prompt.py` | `SystemPromptBuilder` 追加 "# Available Skills" 段（name + description） |
| `src/my_agent/__main__.py` | 识别 `/skill-name ...` 前缀，直接注入 body 到第一条 user 消息 |

### Skill 文件格式

```markdown
---
name: review-pr
description: Summarize a PR diff and flag risky changes. Use when the user asks for a code review or "look at this diff".
---

# PR Review

When reviewing:
1. Start with a 2-line summary of intent.
2. Flag: breaking API changes, untested logic, large function additions.
3. Don't comment on style unless it hides a bug.
...
```

### 关键 API

```python
# skills/loader.py
class Skill(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str
    body: str
    path: Path                        # 用于日志/调试
    source: Literal["global", "project"]

class SkillLoader:
    def __init__(
        self,
        global_root: Path = Path.home() / ".my-agent" / "skills",
        project_root: Path | None = None,   # {cwd}/.my-agent/skills
    ) -> None: ...

    def load_all(self) -> list[Skill]:
        """扫两个 root。同名 skill：project 覆盖 global。"""

    def get(self, name: str) -> Skill | None: ...

# tools/invoke_skill.py
class InvokeSkillInput(BaseModel):
    name: str
    context: str = ""                 # 可选附加背景，拼到 body 末尾

class InvokeSkillTool(Tool[InvokeSkillInput]):
    name = "invoke_skill"
    description = (
        "Load a skill by name. The skill's body contains detailed instructions "
        "for a specific task. Use this when your current task matches one of "
        "the listed skills. Returns the skill body for you to follow."
    )

    def __init__(self, loader: SkillLoader) -> None: ...

    async def execute(self, input: InvokeSkillInput) -> str:
        skill = self._loader.get(input.name)
        if skill is None:
            return f"ERROR: no skill named '{input.name}'. Available: {...}"
        return skill.body + (f"\n\n## Context\n{input.context}" if input.context else "")
```

### System prompt 中的 skill index

```
# Available Skills
You can invoke any of these via the `invoke_skill` tool by name:

- **translate-en** — Translate the given text into English, keeping code and identifiers intact.
- **review-pr** — Summarize a PR diff and flag risky changes. ...
- **writing-plans** — Turn a vague goal into an implementation plan with tasks and success criteria.
```

### CLI 前缀触发

`__main__.py` 在 prompt 解析前做一次预处理：

```python
def preprocess_prompt(prompt: str, loader: SkillLoader) -> str:
    if not prompt.startswith("/"):
        return prompt
    head, _, rest = prompt[1:].partition(" ")
    skill = loader.get(head)
    if skill is None:
        return prompt                 # 没匹配到就原样传
    return f"{skill.body}\n\n## User input\n{rest or '(none)'}"
```

两条路径并存：
- `/skill-name ...` — 用户显式强制用某 skill
- 模型自主调 `invoke_skill` — 根据 description 判断

### 冲突解决 & 排序

- 同名：`project > global`（启动日志告知被覆盖）
- 列表顺序：按 name 字母序（稳定输出，便于测试）
- 名字重复用第一个读到的 + warning log

## 验收标准

- [ ] Loader 能正确解析 frontmatter，容忍缺失 description（报 warning 不 crash）
- [ ] System prompt 里看到所有 skill 的 name + description（只这两项）
- [ ] `invoke_skill("translate-en")` 返回的 body 能让模型按 skill 指令工作
- [ ] `/skill-name ...` CLI 前缀触发等价于模型主动 `invoke_skill`
- [ ] 项目级 skill 同名覆盖全局级，启动日志提示
- [ ] 单元测试覆盖：加载、冲突、invoke、CLI 前缀
- [ ] eval ≥ 2 个 case
- [ ] mypy/ruff 全绿

## Eval Cases

- `skill-invoked-via-model.yaml` — 任务描述匹配 skill description，断言模型调了 `invoke_skill`
- `skill-slash-trigger.yaml` — 用 `/translate-en ...` 触发，断言输出符合 skill 内规则

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 7 段（7.1–7.7）**。

## 变更历史

- 2026-04-19：首版起草。
