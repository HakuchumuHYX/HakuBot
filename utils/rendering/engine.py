"""Chromium 渲染的公共入口。

模板归各插件自己的 templates/ 目录所有，这里只负责“模板 → HTML → 截图”。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import jinja2
from nonebot import logger
from playwright.async_api import Page

from utils.paths import project_root
from utils.rendering.browser import browser_pool
from utils.rendering.fonts import font_css


def is_night(now: datetime | None = None) -> bool:
    """上海时间 18:00–06:00 为夜间，模板据此切换深色配色。"""
    hour = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).hour
    return not 6 <= hour < 18


@asynccontextmanager
async def _loaded_page(
    html: str, *, base_dir: Path, width: int, scale: float, timeout: float
) -> AsyncIterator[Page]:
    """打开页面并等字体、图片就绪；timeout 同时限制页面加载（含远程图片）和截图。"""
    async with browser_pool.page(
        viewport={"width": width, "height": 10}, device_scale_factor=scale
    ) as page:
        page.set_default_timeout(timeout)
        page.on("console", lambda msg: logger.debug(f"[Browser Console]: {msg.text}"))
        await page.goto(base_dir.resolve().as_uri() + "/")
        await page.set_content(html, wait_until="load")
        await page.evaluate("document.fonts.ready")
        await page.evaluate(
            "Promise.all(Array.from(document.images).map(i => i.decode().catch(() => {})))"
        )
        yield page


async def render_html(
    html: str,
    *,
    base_dir: Path = project_root(),
    width: int = 800,
    scale: float = 2,
    type: Literal["jpeg", "png"] = "png",
    quality: int | None = None,
    timeout: float = 30_000,
) -> bytes:
    """HTML 转图片；base_dir 决定模板里相对路径（CSS、图片）的解析位置。"""
    async with _loaded_page(
        html, base_dir=base_dir, width=width, scale=scale, timeout=timeout
    ) as page:
        return await page.screenshot(
            full_page=True, type=type, quality=quality, timeout=timeout
        )


@cache
def _plugin_env(template_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=jinja2.select_autoescape(),
        enable_async=True,
    )


async def render_template(
    template_dir: Path,
    name: str,
    /,
    *,
    width: int,
    scale: float = 2,
    type: Literal["jpeg", "png"] = "png",
    quality: int | None = None,
    timeout: float = 30_000,
    **context,
) -> bytes:
    """渲染插件自己的 Jinja2 模板。

    模板额外可用 font_css（本地 MiSans，family 名为 Haku）和 dark（是否夜间）；
    调用方显式传入同名参数时以调用方为准。
    """
    return await render_html(
        await _template_html(template_dir, name, context),
        base_dir=template_dir,
        width=width,
        scale=scale,
        type=type,
        quality=quality,
        timeout=timeout,
    )


async def render_template_pages(
    template_dir: Path,
    name: str,
    /,
    *,
    width: int,
    paginate: str,
    scale: float = 2,
    type: Literal["jpeg", "png"] = "jpeg",
    quality: int | None = 85,
    timeout: float = 30_000,
    **context,
) -> list[bytes]:
    """渲染插件模板并按页截图，长文分多张图发。

    分页由页面自己算：paginate 是一段 JS 表达式，返回 [{y, height}, ...]（CSS 像素），
    只有页面知道哪里是标题、哪里能断开。整篇只加载一次，各页逐段截取。
    """
    async with _loaded_page(
        await _template_html(template_dir, name, context),
        base_dir=template_dir,
        width=width,
        scale=scale,
        timeout=timeout,
    ) as page:
        regions = await page.evaluate(paginate)
        return [
            await page.screenshot(
                clip={"x": 0, "y": r["y"], "width": width, "height": r["height"]},
                full_page=True,
                type=type,
                quality=quality,
                timeout=timeout,
            )
            for r in regions
        ]


async def _template_html(template_dir: Path, name: str, context: dict) -> str:
    template = _plugin_env(template_dir.resolve()).get_template(name)
    return await template.render_async(
        **{"font_css": font_css(), "dark": is_night(), **context}
    )
