"""Chromium 渲染的公共入口。

业务模板归各插件自己的 templates/ 目录所有，这里只负责“模板 → HTML → 截图”。
本目录的 templates/ 只放通用的 Markdown / 纯文本转图模板。
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import jinja2
import markdown
from nonebot import logger

from utils.paths import project_root
from utils.rendering.browser import browser_pool
from utils.rendering.fonts import font_css

TEMPLATES_PATH = Path(__file__).parent / "templates"

# 通用转换器的模板输入本身就是 HTML（markdown 产物、bison 文本卡片），不能转义。
_builtin_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_PATH), enable_async=True
)


def is_night(now: datetime | None = None) -> bool:
    """上海时间 18:00–06:00 为夜间，模板据此切换深色配色。"""
    hour = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).hour
    return not 6 <= hour < 18


def read_tpl(name: str) -> str:
    return (TEMPLATES_PATH / name).read_text(encoding="utf-8")


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
    """HTML 转图片；base_dir 决定模板里相对路径（CSS、图片）的解析位置。

    timeout 同时限制页面加载（含远程图片）和截图。
    """
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
        return await page.screenshot(
            full_page=True, type=type, quality=quality, timeout=timeout
        )


@lru_cache(maxsize=None)
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
    template = _plugin_env(template_dir.resolve()).get_template(name)
    html = await template.render_async(
        **{"font_css": font_css(), "dark": is_night(), **context}
    )
    return await render_html(
        html,
        base_dir=template_dir,
        width=width,
        scale=scale,
        type=type,
        quality=quality,
        timeout=timeout,
    )


async def render_markdown(md: str, *, css_path: str = "", width: int = 500) -> bytes:
    """Markdown 转图片，默认 GitHub 样式；css_path 可整体替换样式表。"""
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
        extra = (
            f'<style type="text/css">{read_tpl("katex/katex.min.b64_fonts.css")}</style>'
            f"<script defer>{read_tpl('katex/katex.min.js')}</script>"
            f"<script defer>{read_tpl('katex/mhchem.min.js')}</script>"
            f"<script defer>{read_tpl('katex/mathtex-script-type.min.js')}</script>"
        )

    if css_path:
        css = Path(css_path).read_text(encoding="utf-8")
    else:
        css = read_tpl("github-markdown-light.css") + read_tpl("pygments-default.css")

    html = await _builtin_env.get_template("markdown.html").render_async(
        md=md, css=css, extra=extra
    )
    return await render_html(html, base_dir=TEMPLATES_PATH, width=width)


async def render_text(text: str, *, width: int = 500) -> bytes:
    """多行纯文本转图片（bili_dyn_sub 依赖此模板与 bison 的像素一致）。"""
    html = await _builtin_env.get_template("text.html").render_async(
        text=text, css=read_tpl("text.css")
    )
    return await render_html(html, base_dir=TEMPLATES_PATH, width=width)
