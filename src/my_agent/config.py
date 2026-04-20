"""
应用配置模块。

架构角色：
    整个项目唯一读取环境变量和 .env 文件的地方。
    其他所有模块从这里导入 Config 实例，不直接读 os.environ。
    health_check() 在 CLI 启动时被调用，确保 CPA（本地 Anthropic 代理）可达。

学习点：
    - Pydantic BaseModel 作为配置容器（不可变、自动校验、repr 脱敏）
    - python-dotenv 的 load_dotenv()：从 .env 文件把键值对写入 os.environ
    - asyncio.get_running_loop() + run_in_executor：在 async 函数里安全地跑阻塞 I/O
    - urllib.request（标准库）：不引入额外依赖做简单 HTTP GET
"""

from __future__ import annotations

import asyncio
import os
import urllib.request

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


class Config(BaseModel):
    """不可变的运行时配置，从环境变量构建。

    设计原因：
        用 Pydantic BaseModel 而不是普通 dataclass，是为了：
        1. 自动类型校验（传错类型立刻报错，不是运行时才崩）
        2. Field(repr=False) 让 api_key 不出现在 repr/log 输出中，防止泄露
        3. frozen=True 保证配置在运行时不可修改（防止意外突变）

    Pydantic 学习点：
        ConfigDict(frozen=True) 等价于旧版的 class Config: allow_mutation = False。
        frozen 的 Model 是 hashable 的，可以放进 set/dict 作为 key。
    """

    # frozen=True：实例创建后字段不可修改，类似 Python 的 frozen dataclass
    model_config = ConfigDict(frozen=True)

    llm_base_url: str = "http://localhost:8317"

    # Field(repr=False)：这个字段不会出现在 repr() 输出里
    # 作用：防止 print(config) 或日志里意外打印 API key
    llm_api_key: str = Field(repr=False)

    model: str = "claude-sonnet-4-5-20250929"
    debug: bool = False
    log_level: str = "INFO"


def load_config() -> Config:
    """从 .env 文件和环境变量加载配置，返回 Config 实例。

    加载顺序（优先级从高到低）：
        1. 进程已有的环境变量（如 shell export、CI 注入）
        2. .env 文件里的键值（load_dotenv 默认 override=False，不覆盖已有变量）

    Raises:
        KeyError: MY_AGENT_LLM_API_KEY 未设置时，os.environ["..."] 直接抛出。
                  这是故意的硬失败——没有 API key 不应该静默降级。
    """
    # load_dotenv() 把当前目录或父目录的 .env 文件里的变量写入 os.environ
    # override=False（默认）：如果变量已存在，不覆盖；测试里用 monkeypatch 提前设好变量，
    # 就能让 load_dotenv 的 .env 文件不干扰测试结果
    load_dotenv()

    return Config(
        llm_base_url=os.environ.get("MY_AGENT_LLM_BASE_URL", "http://localhost:8317"),
        # 用 os.environ["KEY"] 而不是 .get()：缺失时立刻 KeyError，快速失败
        llm_api_key=os.environ["MY_AGENT_LLM_API_KEY"],
        model=os.environ.get("MY_AGENT_MODEL", "claude-sonnet-4-5-20250929"),
        # 把字符串 "1"/"true"/"yes" 转成 bool，支持常见的 shell 真值写法
        debug=os.environ.get("MY_AGENT_DEBUG", "").lower() in ("1", "true", "yes"),
        log_level=os.environ.get("MY_AGENT_LOG_LEVEL", "INFO"),
    )


async def health_check(config: Config) -> None:
    """向 CPA 发 GET /v1/models，验证本地代理可达。

    asyncio 学习点：
        urllib.request.urlopen 是同步阻塞的，直接在 async 函数里 await 它会
        阻塞整个事件循环。正确做法是用 loop.run_in_executor() 把它扔进线程池，
        让事件循环在等待网络时可以处理其他 coroutine。

    Args:
        config: 包含 base_url 和 api_key 的配置对象。

    Raises:
        RuntimeError: CPA 不可达（未启动、端口错误、网络问题）时抛出，
                      携带可执行的排查提示。
    """
    url = f"{config.llm_base_url.rstrip('/')}/v1/models"

    def _request() -> None:
        """同步版请求函数，将在线程池中执行（非 async）。"""
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.llm_api_key}"})
        try:
            # timeout=5：最多等 5 秒，防止 CPA 假死时卡住启动流程
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except Exception as exc:
            raise RuntimeError(
                f"CPA health check failed: {url} unreachable.\n"
                "Please ensure CLIProxyAPI is running "
                "(/path/to/CLIProxyAPI_*/cli-proxy-api).\n"
                f"Detail: {exc}"
            ) from exc

    # get_running_loop()：获取当前 async 上下文的事件循环
    # run_in_executor(None, fn)：None 表示用默认线程池，fn 是同步函数
    # await 等待线程完成，期间事件循环可以做其他事
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _request)
