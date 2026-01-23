import asyncio
import aiohttp
import os
from typing import Optional
from nonebot import get_driver
from playwright.async_api import async_playwright, Browser, Playwright, BrowserContext, Page, Error as PlaywrightError
from PIL import Image
from io import BytesIO
import tenacity
from tenacity import retry, stop_after_attempt, wait_fixed

from .tools import get_logger, TempFilePath, truncate, get_exc_desc

logger = get_logger("Network")

class HttpError(Exception):
    def __init__(self, status_code: int = 500, message: str = ''):
        self.status_code = status_code
        self.message = message

    def __str__(self):
        return f"{self.status_code}: {truncate(self.message, 512)}"

# ============================ http session ============================ #

_global_client_session: Optional[aiohttp.ClientSession] = None

def get_client_session() -> aiohttp.ClientSession:
    global _global_client_session
    if _global_client_session is None or _global_client_session.closed:
        _global_client_session = aiohttp.ClientSession()
    return _global_client_session

@get_driver().on_shutdown
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

@get_driver().on_shutdown
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
    async with get_client_session().get(url, verify_ssl=False) as resp:
        if resp.status != 200:
            raise HttpError(resp.status, f"下载文件 {truncate(url, 32)} 失败: {resp.reason}")
        with open(file_path, 'wb') as f:
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
    async with get_client_session().get(image_url, verify_ssl=False) as resp:
        if resp.status != 200:
            logger.error(f"下载图片 {image_url} 失败: {resp.status} {resp.reason}")
            raise HttpError(resp.status, f"下载图片 {image_url} 失败")
        image_data = await resp.read()
        return Image.open(BytesIO(image_data))
