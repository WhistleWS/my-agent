# Stage 4 · Context & Memory

**状态**: 🔴 not-started
**依赖**: Stage 3
**预计规模**: ~900–1100 行新增 / ~200 行修改（本 stage 最重）

---

## 目标

让 agent 突破单次对话长度、拥有长期记忆、支持跨会话继续。三大子主题捆在一起做，因为它们同属"上下文工程"：

- **4a — 项目上下文注入**：自动发现并注入 `AGENT.md`（类似 Claude Code 的 `CLAUDE.md`）
- **4b — Memory 系统**：`~/.my-agent/memory/` 下 frontmatter markdown 文件；相关性排序；`save_memory` 工具让 agent 自己写
- **4c — 历史压缩 + Session 持久化**：token 接近模型上限时自动压缩；会话存 JSONL，支持 `--resume`

验收时：

```bash
# 首次跑，agent 记住一条事实
uv run my-agent "记住：我偏好 snake_case 命名和 type hints"

# 新会话，它应该记得
uv run my-agent "写一个读配置的函数"
# ↑ 应该看到 agent 用 snake_case + type hints

# 长对话自动压缩
uv run my-agent --resume <session-id> "..."  # 继续任意历史 session
```

## 学习点

### Python

- **Frontmatter 解析**：`python-frontmatter` 或手写（学习 YAML frontmatter 格式）
- **Token 计数**：`anthropic.Anthropic().messages.count_tokens(...)`
- **JSONL** 读写模式
- **`Path.home()` / XDG** 目录约定
- **模板字符串 / Jinja2** 做 prompt 拼装（或手写 f-string + textwrap）

### Agent

- **上下文分层**：
  ```
  [system prompt]
    - 身份 / 原则
    - 项目 AGENT.md（静态注入）
    - 相关 memory（动态挑选）
    - 可用 skill 列表（Stage 7 会填进来）
  [history]
    - 压缩前摘要（如果压缩过）
    - 最近 N 轮完整消息
  ```
- **相关性排序**：为什么基于 `description` 字段匹配查询，而不是全文搜索
- **压缩策略**：保留最近 N 轮 + 前面全部压成一条摘要 vs 滑动窗口 vs 分层摘要；选哪种、为什么
- **Memory 的双向性**：人类写给 agent（用户偏好、项目约定）+ agent 写给自己（从对话中学到的事实）
- **Session 续命**：不只是"存消息"，还要存 `MessageHistory + 上次压缩状态 + 累计 token`

## 设计

### 新增文件

| 子阶段 | 文件 | 职责 |
|---|---|---|
| 4a | `src/my_agent/context/__init__.py` | 占位 |
| 4a | `src/my_agent/context/project.py` | 递归向上找 `AGENT.md`，拼到 system prompt |
| 4b | `src/my_agent/memory/__init__.py` | 占位 |
| 4b | `src/my_agent/memory/store.py` | `MemoryStore`：扫描、读 frontmatter、写入新 memory |
| 4b | `src/my_agent/memory/ranking.py` | `rank_memories(query, memories) -> list[Memory]`：简易相关性 |
| 4b | `src/my_agent/tools/save_memory.py` | `save_memory` 工具 |
| 4c | `src/my_agent/context/compaction.py` | Token 计数 + 压缩触发 + 摘要 |
| 4c | `src/my_agent/core/session.py` | `SessionStore`：存/恢复会话 JSONL |
| 共 | `src/my_agent/core/prompt.py` | `SystemPromptBuilder`：把上面所有分片拼装 |
| 共 | `tests/test_stage_4_*.py` | 各子阶段测试 |
| 共 | `evals/cases/stage-4/*.yaml` | 含跨会话记忆 case |

### 修改文件

| 文件 | 修改点 |
|---|---|
| `src/my_agent/core/loop.py` | 使用 `SystemPromptBuilder`；每轮后检查压缩；session 存盘 |
| `src/my_agent/__main__.py` | `--resume <session-id>`；`--new-session`；首次启动生成 session id |

### 关键 API

#### 4a — 项目上下文

```python
# context/project.py
def find_agent_md(start: Path) -> Path | None:
    """从 start 开始向父目录递归查找 AGENT.md，到用户 home 停止。"""

def load_project_context(cwd: Path) -> str | None:
    """返回 AGENT.md 的内容（可能含多级合并：repo-root / submodule）。"""
```

#### 4b — Memory

```python
# memory/store.py
class Memory(BaseModel):
    name: str
    description: str                    # 用于相关性匹配的关键信号
    type: Literal["user", "feedback", "project", "reference"]
    body: str
    path: Path
    mtime: datetime

class MemoryStore:
    def __init__(self, root: Path = Path.home() / ".my-agent" / "memory") -> None: ...

    def scan(self) -> list[Memory]: ...
    def write(self, memory: Memory) -> None: ...
    def delete(self, name: str) -> None: ...

# memory/ranking.py
def rank_memories(
    query: str, memories: list[Memory], top_k: int = 10
) -> list[Memory]:
    """
    初版用简单加权：description 包含 query 关键词 → 高分；type=user 略加权；新鲜度略加权。
    后续可替换为 embedding（YAGNI，先不做）。
    """

# tools/save_memory.py
class SaveMemoryInput(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]+$")
    description: str
    type: Literal["user", "feedback", "project", "reference"]
    body: str

class SaveMemoryTool(Tool[SaveMemoryInput]):
    name = "save_memory"
    description = "Save a long-term memory readable in future sessions."
```

#### 4c — 压缩 + Session

```python
# context/compaction.py
class CompactionPolicy(BaseModel):
    trigger_ratio: float = 0.7        # 当 token ≥ 模型上下文 × 0.7 → 触发
    keep_last_turns: int = 6          # 保留最近 N 轮原样
    summary_target_tokens: int = 2000

class Compactor:
    def __init__(self, client: AnthropicClient, policy: CompactionPolicy) -> None: ...

    async def maybe_compact(self, history: MessageHistory) -> bool:
        """返回 True 表示本次发生了压缩。"""

# core/session.py
class Session(BaseModel):
    id: str                           # ulid or iso timestamp + hash
    created_at: datetime
    model: str
    cwd: str
    history: list[dict]
    compactions: list[CompactionRecord] = []
    usage: TokenUsage

class SessionStore:
    def __init__(self, root: Path = Path.home() / ".my-agent" / "sessions") -> None: ...

    def new(self) -> Session: ...
    def load(self, session_id: str) -> Session: ...
    def append(self, session: Session, message: dict) -> None:
        """JSONL 追加写。"""
```

#### System Prompt Builder

```python
# core/prompt.py
class SystemPromptBuilder:
    def __init__(
        self,
        memory_store: MemoryStore,
        project_context: str | None,
    ) -> None: ...

    def build(self, user_query: str) -> str:
        sections = [
            self._identity(),
            self._global_rules(),
        ]
        if self._project_context:
            sections.append(self._wrap("# Project Context (AGENT.md)", self._project_context))
        relevant = rank_memories(user_query, self._memory_store.scan())
        if relevant:
            sections.append(self._wrap("# Relevant Memories", self._format_memories(relevant)))
        return "\n\n".join(sections)
```

### 目录结构（运行时）

```
~/.my-agent/
├── memory/
│   ├── MEMORY.md                       # 索引（optional，便于用户浏览）
│   ├── user-prefs-naming.md
│   ├── project-ingest-pipeline.md
│   └── ...
└── sessions/
    ├── 2026-04-19T14-32-10Z.jsonl
    └── 2026-04-19T15-01-22Z.jsonl
```

## 验收标准

- [ ] 4a：项目根放 `AGENT.md`，agent 启动日志显示"注入了 N 字节项目上下文"
- [ ] 4b：`save_memory` 工具把一条"用户偏好 snake_case"写到 `~/.my-agent/memory/*.md`
- [ ] 4b：新 session 启动时，`ranking` 在 query 相关时能把该 memory 排进 top 5
- [ ] 4c：构造一个长对话（FakeLLM 喂很多 turn），观察 token 到达阈值时触发压缩；压缩后 `history` 最近 N 轮保留，之前合并为一条 summary message
- [ ] 4c：`uv run my-agent --resume <id> "..."` 能恢复历史
- [ ] eval 包含一个**跨会话**记忆 case：session A 存一条 memory → session B 的 prompt 里能看到它
- [ ] mypy/ruff 全绿

## Eval Cases

- `inject-agent-md.yaml` — `AGENT.md` 里写"测试用 pytest，不用 unittest"，agent 写测试应采纳
- `save-and-recall.yaml` — 两步 case：step 1 让 agent `save_memory`；step 2 在新 session 验证 recall
- `auto-compaction.yaml` — 人造超长历史，断言 `compactions.count >= 1` 且回答仍正确

## 任务引用

本阶段子任务的权威来源：`SPEC.md` → 详细任务表 → **Stage 4 段（4.1–4.11）**。

## 变更历史

- 2026-04-19：首版起草。
