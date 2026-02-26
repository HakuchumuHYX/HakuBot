"""
统一 Playwright 浏览器管理模块。

功能：
- 全局单例浏览器，所有插件共享
- 空闲自动关闭（最后一个 context 关闭后 IDLE_TIMEOUT 秒无新请求则关闭浏览器）
- 提供 html_to_pic / template_to_pic / md_to_pic / get_new_page 等函数
  （接口兼容 nonebot_plugin_htmlrender，可直接替换 import）
- 保留 PlaywrightPage 上下文管理器（向后兼容）
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from os import getcwd
from pathlib import Path
from typing import Any, Literal, Optional, Union

import aiofiles
import jinja2
import markdown
from playwright.async_api import (
    Browser,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
    Error as PlaywrightError,
)

from .tools import get_logger, get_exc_desc

logger = get_logger("Browser")

# ================= 配置 =================

IDLE_TIMEOUT = 300  # 空闲 5 分钟后自动关闭浏览器
MAX_CONTEXTS = 5

TEMPLATES_PATH = str(Path(__file__).parent / "templates")

# ================= 全局状态 =================

_playwright_instance: Optional[Playwright] = None
_browser_type: Optional[BrowserType] = None
_playwright_browser: Optional[Browser] = None
_using_htmlrender: bool = False  # 是否复用 htmlrender 的浏览器

_context_semaphore = asyncio.Semaphore(MAX_CONTEXTS)
_active_contexts = 0  # 当前活跃的 context 数量
_state_lock = asyncio.Lock()
_idle_task: Optional[asyncio.Task] = None
_last_release_time: float = 0.0


# ================= 浏览器生命周期 =================

async def _ensure_browser() -> Browser:
    """确保浏览器已启动，返回 Browser 实例。调用前需持有 _state_lock。"""
    global _playwright_instance, _browser_type, _playwright_browser, _idle_task, _using_htmlrender

    if _playwright_browser is not None and _playwright_browser.is_connected():
        # 取消空闲关闭定时器
        if _idle_task is not None and not _idle_task.done():
            _idle_task.cancel()
            _idle_task = None
        return _playwright_browser

    # 优先复用 nonebot_plugin_htmlrender 的浏览器实例
    try:
        from nonebot_plugin_htmlrender.browser import get_browser as _hr_get_browser
        browser = await _hr_get_browser()
        if browser and browser.is_connected():
            _playwright_browser = browser
            _using_htmlrender = True
            if _idle_task is not None and not _idle_task.done():
                _idle_task.cancel()
                _idle_task = None
            logger.info("复用 nonebot_plugin_htmlrender 的浏览器实例")
            return _playwright_browser
    except Exception as e:
        logger.debug(f"无法复用 htmlrender 浏览器，将自行启动: {e}")

    # Fallback: 自行启动浏览器
    _using_htmlrender = False
    if _playwright_instance is None:
        _playwright_instance = await async_playwright().start()
        _browser_type = _playwright_instance.chromium
        logger.info("初始化 Playwright 异步 API")

    _playwright_browser = await _browser_type.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    logger.info("启动 Playwright Browser（独立实例）")

    # 取消可能残留的空闲定时器
    if _idle_task is not None and not _idle_task.done():
        _idle_task.cancel()
        _idle_task = None

    return _playwright_browser


async def _close_browser():
    """关闭浏览器实例（不关闭 Playwright runtime，下次可快速重启）。"""
    global _playwright_browser, _using_htmlrender
    if _playwright_browser is not None:
        if _using_htmlrender:
            # 复用的 htmlrender 浏览器，不由我们关闭，只解除引用
            _playwright_browser = None
            _using_htmlrender = False
            logger.info("已释放 htmlrender 浏览器引用")
            return
        try:
            if _playwright_browser.is_connected():
                await _playwright_browser.close()
                logger.info("已关闭空闲 Playwright Browser")
        except Exception as e:
            logger.error(f"关闭 Playwright Browser 失败: {get_exc_desc(e)}")
        finally:
            _playwright_browser = None


async def _idle_closer():
    """空闲定时关闭协程。"""
    try:
        await asyncio.sleep(IDLE_TIMEOUT)
        async with _state_lock:
            if _active_contexts == 0:
                await _close_browser()
    except asyncio.CancelledError:
        pass


async def _on_context_acquire():
    """context 被获取时调用。"""
    global _active_contexts, _idle_task
    async with _state_lock:
        _active_contexts += 1
        if _idle_task is not None and not _idle_task.done():
            _idle_task.cancel()
            _idle_task = None


async def _on_context_release():
    """context 被释放时调用。"""
    global _active_contexts, _idle_task, _last_release_time
    async with _state_lock:
        _active_contexts = max(0, _active_contexts - 1)
        _last_release_time = time.monotonic()
        if _active_contexts == 0:
            # 启动空闲关闭定时器
            if _idle_task is not None and not _idle_task.done():
                _idle_task.cancel()
            _idle_task = asyncio.create_task(_idle_closer())


# ================= get_new_page（兼容 htmlrender 接口）=================

@asynccontextmanager
async def get_new_page(device_scale_factor: float = 2, **kwargs) -> AsyncIterator[Page]:
    """
    获取一个新页面的异步上下文管理器。
    接口兼容 nonebot_plugin_htmlrender.get_new_page。
    """
    await _context_semaphore.acquire()
    browser = None
    context = None
    page = None
    try:
        async with _state_lock:
            browser = await _ensure_browser()
        await _on_context_acquire()

        context = await browser.new_context(device_scale_factor=device_scale_factor, **kwargs)
        page = await context.new_page()
        yield page
    except PlaywrightError as pe:
        # 浏览器可能崩溃，标记为需要重启
        global _playwright_browser
        if browser is not None and not _using_htmlrender:
            try:
                await browser.close()
            except Exception:
                pass
        _playwright_browser = None
        raise pe
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception as e:
                logger.error(f"关闭 Page 失败: {get_exc_desc(e)}")
        if context is not None:
            try:
                await context.close()
            except Exception as e:
                logger.error(f"关闭 Context 失败: {get_exc_desc(e)}")
        await _on_context_release()
        _context_semaphore.release()


# ================= PlaywrightPage（向后兼容）=================

class PlaywrightPage:
    """
    异步上下文管理器，用于管理 Playwright 的 context。
    保留向后兼容，内部使用全局共享浏览器。
    """

    def __init__(self, context_options: dict | None = None):
        self.context = None
        self.page: Page | None = None
        self.context_options: dict = context_options if context_options is not None else {
            "locale": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        }

    async def __aenter__(self) -> Page:
        await _context_semaphore.acquire()
        try:
            async with _state_lock:
                browser = await _ensure_browser()
            await _on_context_acquire()
            self.context = await browser.new_context(**self.context_options)
        except PlaywrightError as pe:
            global _playwright_browser
            if not _using_htmlrender:
                try:
                    if _playwright_browser is not None:
                        await _playwright_browser.close()
                except Exception as e:
                    logger.error(f"关闭 Playwright Browser 失败 {get_exc_desc(e)}")
            _playwright_browser = None
            await _on_context_release()
            _context_semaphore.release()
            raise pe
        except Exception:
            await _on_context_release()
            _context_semaphore.release()
            raise
        self.page = await self.context.new_page()
        return self.page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
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
        await _on_context_release()
        _context_semaphore.release()
        self.page = None
        self.context = None
        return False


# ================= 文件/模板读取 =================

async def read_file(path: str) -> str:
    async with aiofiles.open(path, encoding="UTF8") as f:
        return await f.read()


async def read_tpl(path: str) -> str:
    return await read_file(f"{TEMPLATES_PATH}/{path}")


# ================= jinja2 环境 =================

_env = jinja2.Environment(
    extensions=["jinja2.ext.loopcontrols"],
    loader=jinja2.FileSystemLoader(TEMPLATES_PATH),
    enable_async=True,
)


# ================= html_to_pic =================

async def html_to_pic(
    html: str,
    wait: int = 0,
    template_path: str = f"file://{getcwd()}",
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
    full_page: Optional[bool] = True,
    **kwargs,
) -> bytes:
    """html 转图片，接口兼容 nonebot_plugin_htmlrender。"""
    if "file:" not in template_path:
        raise Exception("template_path should be file:///path/to/template")
    async with get_new_page(device_scale_factor, **kwargs) as page:
        page.on("console", lambda msg: logger.debug(f"[Browser Console]: {msg.text}"))
        await page.goto(template_path)
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_timeout(wait)
        return await page.screenshot(
            full_page=full_page,
            type=type,
            quality=quality,
            timeout=screenshot_timeout,
        )


# ================= template_to_pic =================

async def template_to_pic(
    template_path: str,
    template_name: str,
    templates: dict[Any, Any],
    filters: Optional[dict[str, Any]] = None,
    pages: Optional[dict[Any, Any]] = None,
    wait: int = 0,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """使用 jinja2 模板引擎通过 html 生成图片，接口兼容 nonebot_plugin_htmlrender。"""
    if pages is None:
        pages = {
            "viewport": {"width": 500, "height": 10},
            "base_url": f"file://{getcwd()}",
        }

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path),
        enable_async=True,
    )

    if filters:
        for filter_name, filter_func in filters.items():
            template_env.filters[filter_name] = filter_func

    template = template_env.get_template(template_name)

    return await html_to_pic(
        template_path=f"file://{template_path}",
        html=await template.render_async(**templates),
        wait=wait,
        type=type,
        quality=quality,
        device_scale_factor=device_scale_factor,
        screenshot_timeout=screenshot_timeout,
        **pages,
    )


# ================= template_to_html =================

async def template_to_html(
    template_path: str,
    template_name: str,
    filters: Optional[dict[str, Any]] = None,
    **kwargs,
) -> str:
    """使用 jinja2 模板引擎渲染 html 字符串。"""
    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path),
        enable_async=True,
    )
    if filters:
        for filter_name, filter_func in filters.items():
            template_env.filters[filter_name] = filter_func
    template = template_env.get_template(template_name)
    return await template.render_async(**kwargs)


# ================= md_to_pic =================

async def md_to_pic(
    md: str = "",
    md_path: str = "",
    css_path: str = "",
    width: int = 500,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """markdown 转图片，接口兼容 nonebot_plugin_htmlrender。"""
    template = _env.get_template("markdown.html")
    if not md:
        if md_path:
            md = await read_file(md_path)
        else:
            raise Exception("md or md_path must be provided")

    md = markdown.markdown(
        md,
        extensions=[
            "pymdownx.tasklist",
            "tables",
            "fenced_code",
            "codehilite",
            "mdx_math",
            "pymdownx.tilde",
        ],
        extension_configs={"mdx_math": {"enable_dollar_delimiter": True}},
    )

    extra = ""
    if "math/tex" in md:
        katex_css = await read_tpl("katex/katex.min.b64_fonts.css")
        katex_js = await read_tpl("katex/katex.min.js")
        mhchem_js = await read_tpl("katex/mhchem.min.js")
        mathtex_js = await read_tpl("katex/mathtex-script-type.min.js")
        extra = (
            f'<style type="text/css">{katex_css}</style>'
            f"<script defer>{katex_js}</script>"
            f"<script defer>{mhchem_js}</script>"
            f"<script defer>{mathtex_js}</script>"
        )

    if css_path:
        css = await read_file(css_path)
    else:
        css = await read_tpl("github-markdown-light.css") + await read_tpl(
            "pygments-default.css",
        )

    return await html_to_pic(
        template_path=f"file://{css_path or TEMPLATES_PATH}",
        html=await template.render_async(md=md, css=css, extra=extra),
        viewport={"width": width, "height": 10},
        type=type,
        quality=quality,
        device_scale_factor=device_scale_factor,
        screenshot_timeout=screenshot_timeout,
    )


# ================= text_to_pic =================

async def text_to_pic(
    text: str,
    css_path: str = "",
    width: int = 500,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """多行文本转图片，接口兼容 nonebot_plugin_htmlrender。"""
    template = _env.get_template("text.html")

    return await html_to_pic(
        template_path=f"file://{css_path or TEMPLATES_PATH}",
        html=await template.render_async(
            text=text,
            css=await read_file(css_path) if css_path else await read_tpl("text.css"),
        ),
        viewport={"width": width, "height": 10},
        type=type,
        quality=quality,
        device_scale_factor=device_scale_factor,
        screenshot_timeout=screenshot_timeout,
    )


# ================= capture_element =================

async def capture_element(
    url: str,
    element: str,
    page_kwargs: Optional[dict] = None,
    goto_kwargs: Optional[dict] = None,
    screenshot_kwargs: Optional[dict] = None,
) -> bytes:
    """捕获网页中指定元素的截图。"""
    page_kwargs = page_kwargs or {}
    goto_kwargs = goto_kwargs or {}
    screenshot_kwargs = screenshot_kwargs or {}

    async with get_new_page(**page_kwargs) as page:
        page.on("console", lambda msg: logger.debug(f"[Browser Console]: {msg.text}"))
        await page.goto(url, **goto_kwargs)
        return await page.locator(element).screenshot(**screenshot_kwargs)
