import asyncio
import os
from playwright.async_api import (
    async_playwright, 
    Browser, 
    Playwright, 
    BrowserType, 
    BrowserContext, 
    Page,
    Error as PlaywrightError
)
from .tools import get_logger, get_exc_desc

logger = get_logger("Browser")

_playwright_instance: Playwright | None = None
_browser_type: BrowserType | None = NotImplementedError
_playwright_browser: Browser | None = None

MAX_CONTEXTS = 5
_context_semaphore = asyncio.Semaphore(MAX_CONTEXTS)

class PlaywrightPage:
    """
    异步上下文管理器，用于管理 Playwright 的context。
    """
    def __init__(self, context_options: dict | None = None):
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.context_options: dict = context_options if context_options is not None else { 
            'locale': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
        }

    async def __aenter__(self) -> Page:
        global _playwright_instance, _browser_type, _playwright_browser
        # 检查浏览器的情况
        if _playwright_browser is None or not _playwright_browser.is_connected():

            if _playwright_instance is None: 
                # 启动async_playwright实例
                _playwright_instance = await async_playwright().start()
                _browser_type = _playwright_instance.chromium
                logger.info("初始化 Playwright 异步 API")
                pass
            # 清除临时文件
            try:
                # Windows上 rm -rf 可能不工作，这里简单略过或者用 shutil
                import shutil
                # if os.path.exists("/tmp/rust_mozprofile*"): ... # Windows路径不同，暂且跳过清理
                pass
            except Exception as e:
                logger.error(f"清空WebDriver临时文件失败: {e}")

            #启动浏览器
            _playwright_browser = await _browser_type.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox'] 
            )
            logger.info(f"启动 Playwright Browser")
            pass
        # 限制context的数量
        await _context_semaphore.acquire()
        try:
            self.context = await _playwright_browser.new_context(**self.context_options)
        except PlaywrightError as pe:
            # 在新建context时就发生异常，可以认为playwright本身出了问题，重启一下
            try:
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
            finally:# 释放信号
                _context_semaphore.release()
        self.page = None
        self.context = None
        return False
