"""
read_file 工具模块。

架构角色：
    ReadFileTool 是 Stage 0 里 agent 唯一能调用的工具，用于验证整个
    tool_use 协议链路：LLM 生成调用 → ToolRegistry.dispatch() → execute() → tool_result 回传。

    数据流位置：
        LLM 生成 tool_use{name="read_file", input={"path": "..."}}
          → ToolRegistry.dispatch("read_file", {"path": "..."})
          → ReadFileInput(path="...") [Pydantic 校验]
          → ReadFileTool.execute(input) → 返回文件内容字符串
          → AgentLoop 包装成 tool_result → 喂回 LLM → 最终文本回答

学习点：
    - pathlib.Path：Python 推荐的文件路径操作方式，比 os.path 更面向对象。
      Path(s).read_text() 是读取文本文件最简洁的写法。
    - encoding="utf-8"：显式指定编码，避免在不同操作系统或 locale 下行为不一致
      （Windows 默认可能是 GBK/CP1252）。
    - 错误传播：不捕获 FileNotFoundError 等异常，让 AgentLoop 捕获并包装成
      is_error=True 的 tool_result。这样 LLM 能看到错误信息并自行决定下一步。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from my_agent.tools.base import Tool


class ReadFileInput(BaseModel):
    """read_file 工具的输入参数模型。

    只有一个字段 path，描述足够让 LLM 生成正确的调用参数。

    Pydantic 字段学习点：
        - path: str 没有 default，是必填字段。LLM 不传就会 ValidationError。
        - Pydantic 自动把这个模型的 JSON Schema 生成为：
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
          这个 schema 通过 Tool.to_anthropic_schema() → input_schema 字段传给 LLM。
    """

    path: str
    """文件路径，相对于当前工作目录（CWD）或绝对路径均可。"""


class ReadFileTool(Tool[ReadFileInput]):
    """读取指定路径文件全部内容，以字符串形式返回。

    这是 Stage 0 的唯一工具，验证从工具注册到执行的完整链路。
    Stage 1 会补充 write_file、edit_file、bash、glob、grep 等工具。
    """

    name = "read_file"
    description = (
        "Read the full text contents of a file at the given path. "
        "Path can be absolute or relative to the current working directory."
    )
    input_model = ReadFileInput

    async def execute(self, input: ReadFileInput) -> str:
        """读取文件并返回内容字符串。

        async def 但实际上是同步 I/O：
            pathlib.Path.read_text() 是阻塞调用，不是真正的 async I/O。
            Stage 0 里文件很小、延迟可忽略，所以暂时接受。
            Stage 1 引入 BashTool 时会展示 asyncio.create_subprocess_exec 的真正异步 I/O。
            （如果需要严格非阻塞，可以改用 asyncio.get_event_loop().run_in_executor(None, ...)，
             参考 config.py 里 health_check 的做法。）

        Args:
            input: 经 Pydantic 校验的输入，input.path 是文件路径字符串。

        Returns:
            文件的完整文本内容（UTF-8 解码）。

        Raises:
            FileNotFoundError: 文件不存在时由 Python 自动抛出，
                               AgentLoop 会捕获并包装成 is_error=True 的 tool_result。
            PermissionError:   无读取权限时同上。
        """
        # Path(input.path) 把字符串转成 pathlib.Path 对象
        # .read_text(encoding="utf-8") 一次性读取全部内容，返回 str
        return Path(input.path).read_text(encoding="utf-8")  # noqa: ASYNC240
