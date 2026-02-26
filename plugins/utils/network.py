import aiohttp
import os
from io import BytesIO
from typing import Optional

from nonebot import get_driver
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_fixed

from .tools import TempFilePath, get_logger, truncate

logger = get_logger("Network")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=30, sock_read=120)


def normalize_proxy(proxy: Optional[str]) -> Optional[str]:
    """
    统一代理格式：
    - 允许填：127.0.0.1:7890
    - 或：http://127.0.0.1:7890 / https://... / socks5://...
    """
    if not proxy:
        return None
    proxy = str(proxy).strip()
    if not proxy:
        return None

    # 兼容误填：proxy="http://127.0.0.1:7890" 又被外层拼接导致 "http://http://..."
    while proxy.startswith("http://http://"):
        proxy = proxy.replace("http://http://", "http://", 1)
    while proxy.startswith("https://https://"):
        proxy = proxy.replace("https://https://", "https://", 1)

    if "://" in proxy:
        return proxy
    return f"http://{proxy}"


_cached_effective_proxy: Optional[str] = None
_logged_effective_proxy: bool = False


def get_effective_proxy(explicit_proxy: Optional[str] = None) -> Optional[str]:
    """
    获取实际要使用的代理（统一入口）：
    1) 如果传入 explicit_proxy：优先使用（适用于“某插件自带 proxy 配置”的情况）
    2) 否则回退读取环境变量 HTTP(S)_PROXY / ALL_PROXY（适配系统全局代理/容器环境）

    注意：
    - 会做简单缓存；但如果显式传入 proxy，则不会走缓存（避免不同调用方冲突）。
    - 首次解析环境变量代理会打印一条日志，方便排查。
    """
    global _cached_effective_proxy, _logged_effective_proxy

    # 显式代理不缓存，直接返回（允许不同插件传不同 proxy）
    explicit_proxy = normalize_proxy(explicit_proxy)
    if explicit_proxy:
        return explicit_proxy

    if _cached_effective_proxy is not None or _logged_effective_proxy:
        return _cached_effective_proxy

    proxy = None
    for key in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        val = os.environ.get(key)
        val = normalize_proxy(val)
        if val:
            proxy = val
            break

    _cached_effective_proxy = proxy
    if not _logged_effective_proxy:
        logger.info(f"effective_proxy(env) = {_cached_effective_proxy}")
        _logged_effective_proxy = True

    return _cached_effective_proxy


class HttpError(Exception):
    def __init__(self, status_code: int = 500, message: str = ""):
        self.status_code = status_code
        self.message = message

    def __str__(self):
        return f"{self.status_code}: {truncate(self.message, 512)}"


# ============================ http session ============================ #

_global_client_session: Optional[aiohttp.ClientSession] = None


def get_client_session() -> aiohttp.ClientSession:
    """
    全局 aiohttp session：
    - trust_env=True: 允许 aiohttp 自己读取环境变量代理
    - timeout: 防止请求在网络不通/被墙时无限等待
    """
    global _global_client_session
    if _global_client_session is None or _global_client_session.closed:
        _global_client_session = aiohttp.ClientSession(
            trust_env=True,
            timeout=DEFAULT_TIMEOUT,
        )
    return _global_client_session


try:
    _driver = get_driver()
except Exception:
    # 允许在非 NoneBot 环境下 import（例如命令行检查/单测）
    _driver = None


if _driver:

    @_driver.on_shutdown
    async def _close_session():
        if _global_client_session is not None and not _global_client_session.closed:
            await _global_client_session.close()


# ============================ Download ============================ #

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_file(url: str, file_path: str, *, proxy: Optional[str] = None):
    """
    下载文件到指定路径
    """
    proxy = get_effective_proxy(proxy)
    async with get_client_session().get(url, verify_ssl=False, proxy=proxy, timeout=DEFAULT_TIMEOUT) as resp:
        if resp.status != 200:
            raise HttpError(resp.status, f"下载文件 {truncate(url, 32)} 失败: {resp.reason}")
        with open(file_path, "wb") as f:
            f.write(await resp.read())


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_bytes(url: str, *, proxy: Optional[str] = None) -> bytes:
    """
    下载 URL 内容并返回 bytes（用于需要“先下载再上传/提交”的场景，例如目标站点不支持直接抓取某些带鉴权/防盗链的图片 URL）
    """
    proxy = get_effective_proxy(proxy)
    async with get_client_session().get(url, verify_ssl=False, proxy=proxy, timeout=DEFAULT_TIMEOUT) as resp:
        if resp.status != 200:
            raise HttpError(resp.status, f"下载内容 {truncate(url, 32)} 失败: {resp.reason}")
        return await resp.read()

class TempDownloadFilePath(TempFilePath):
    def __init__(self, url, ext: str = None, remove_after: bool = True, *, proxy: Optional[str] = None):
        self.url = url
        self.proxy = proxy
        if ext is None:
            # 尝试从url推断后缀
            parts = url.split("?")[0].split(".")
            if len(parts) > 1:
                ext = parts[-1]
            else:
                ext = "tmp"
        super().__init__(ext, remove_after)

    async def __aenter__(self) -> str:
        await download_file(self.url, str(self.path), proxy=self.proxy)
        return super().__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return super().__exit__(exc_type, exc_val, exc_tb)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_image(image_url: str, force_http: bool = False, *, proxy: Optional[str] = None) -> Image.Image:
    """
    下载图片并返回 PIL.Image 对象
    """
    if force_http and image_url.startswith("https"):
        image_url = image_url.replace("https", "http", 1)

    proxy = get_effective_proxy(proxy)
    async with get_client_session().get(
        image_url,
        verify_ssl=False,
        proxy=proxy,
        timeout=DEFAULT_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            logger.error(f"下载图片 {image_url} 失败: {resp.status} {resp.reason}")
            raise HttpError(resp.status, f"下载图片 {truncate(image_url, 64)} 失败")
        image_data = await resp.read()
        return Image.open(BytesIO(image_data))
