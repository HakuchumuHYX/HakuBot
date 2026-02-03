import asyncio
import aiohttp
import os
from typing import Optional
from nonebot import get_driver
from playwright.async_api import (
    async_playwright,
    Browser,
    Playwright,
    BrowserContext,
    Page,
    Error as PlaywrightError,
)
from PIL import Image
from io import BytesIO
import tenacity
from tenacity import retry, stop_after_attempt, wait_fixed

from .config import config
from .tools import get_logger, TempFilePath, truncate, get_exc_desc

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


def get_effective_proxy() -> Optional[str]:
    """
    获取实际要使用的代理（统一入口）：
    1) 优先使用插件配置 config.proxy
    2) 否则回退读取环境变量 HTTP(S)_PROXY / ALL_PROXY（适配“系统全局代理/容器环境”）

    注意：会做简单缓存，并在首次解析时打印一条日志，方便确认“实际用了哪个代理”。
    """
    global _cached_effective_proxy, _logged_effective_proxy

    if _cached_effective_proxy is not None or _logged_effective_proxy:
        return _cached_effective_proxy

    proxy = normalize_proxy(config.get("proxy"))
    if not proxy:
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
        logger.info(f"lunabot_imgexp effective_proxy = {_cached_effective_proxy}")
        _logged_effective_proxy = True

    return _cached_effective_proxy

class HttpError(Exception):
    def __init__(self, status_code: int = 500, message: str = ''):
        self.status_code = status_code
        self.message = message

    def __str__(self):
        return f"{self.status_code}: {truncate(self.message, 512)}"

# ============================ http session ============================ #

_global_client_session: Optional[aiohttp.ClientSession] = None

def get_client_session() -> aiohttp.ClientSession:
    """
    用于本插件所有 aiohttp 请求的全局 session：
    - trust_env=True: 允许读取环境变量代理（HTTP(S)_PROXY）
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

# ============================ Playwright ============================ #

_playwright_instance: Optional[Playwright] = None
_playwright_browser: Optional[Browser] = None

MAX_CONTEXTS = 8
_context_semaphore = asyncio.Semaphore(MAX_CONTEXTS)

class PlaywrightPage:
    """
    异步上下文管理器，用于管理 Playwright 的context。
    """
    def __init__(self, context_options: dict | None = None):
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.context_options: dict = context_options if context_options is not None else { 
            'locale': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        }

    async def __aenter__(self) -> Page:
        global _playwright_instance, _playwright_browser
        
        # 检查浏览器的情况
        if _playwright_browser is None or not _playwright_browser.is_connected():
            if _playwright_instance is None: 
                # 启动async_playwright实例
                _playwright_instance = await async_playwright().start()
                logger.info("初始化 Playwright 异步 API")
            
            # 启动浏览器
            try:
                _playwright_browser = await _playwright_instance.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-setuid-sandbox'] 
                )
                logger.info(f"启动 Playwright Browser")
            except Exception as e:
                logger.error(f"启动 Playwright Browser 失败: {get_exc_desc(e)}")
                raise

        # 限制context的数量
        await _context_semaphore.acquire()
        try:
            self.context = await _playwright_browser.new_context(**self.context_options)
        except PlaywrightError as pe:
            # 在新建context时就发生异常，可以认为playwright本身出了问题，重启一下
            try:
                if _playwright_browser:
                    await _playwright_browser.close()
            except Exception as e:
                logger.error(f"关闭 Playwright Browser 失败 {get_exc_desc(e)}")
            _playwright_browser = None
            _context_semaphore.release()
            raise pe
        except: 
            # 出现异常时释放信号
            _context_semaphore.release()
            raise
        
        self.page = await self.context.new_page()
        return self.page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 关闭上下文，自动清理
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                logger.error(f"关闭 Playwright Page 失败 {get_exc_desc(e)}")
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                logger.error(f"关闭 Playwright Context 失败 {get_exc_desc(e)}")
            finally:
                # 释放信号
                _context_semaphore.release()
        self.page = None
        self.context = None
        return False

if _driver:

    @_driver.on_shutdown
    async def _close_playwright():
        global _playwright_browser, _playwright_instance
        if _playwright_browser:
            await _playwright_browser.close()
            _playwright_browser = None
        if _playwright_instance:
            await _playwright_instance.stop()
            _playwright_instance = None

# ============================ Download ============================ #

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_file(url: str, file_path: str):
    """
    下载文件到指定路径
    """
    proxy = get_effective_proxy()
    async with get_client_session().get(url, verify_ssl=False, proxy=proxy, timeout=DEFAULT_TIMEOUT) as resp:
        if resp.status != 200:
            raise HttpError(resp.status, f"下载文件 {truncate(url, 32)} 失败: {resp.reason}")
        with open(file_path, "wb") as f:
            f.write(await resp.read())

class TempDownloadFilePath(TempFilePath):
    def __init__(self, url, ext: str = None, remove_after: bool = True):
        self.url = url
        if ext is None:
            # 尝试从url推断后缀
            parts = url.split('?')[0].split('.')
            if len(parts) > 1:
                ext = parts[-1]
            else:
                ext = 'tmp'
        super().__init__(ext, remove_after)

    async def __aenter__(self) -> str:
        await download_file(self.url, str(self.path))
        return super().__enter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return super().__exit__(exc_type, exc_val, exc_tb)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
async def download_image(image_url: str, force_http: bool = False) -> Image.Image:
    """
    下载图片并返回PIL.Image对象
    """
    if force_http and image_url.startswith("https"):
        image_url = image_url.replace("https", "http")

    proxy = get_effective_proxy()
    async with get_client_session().get(
        image_url,
        verify_ssl=False,
        proxy=proxy,
        timeout=DEFAULT_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            logger.error(f"下载图片 {image_url} 失败: {resp.status} {resp.reason}")
            raise HttpError(resp.status, f"下载图片 {image_url} 失败")
        image_data = await resp.read()
        return Image.open(BytesIO(image_data))
