from pathlib import Path
from typing import Any, Literal, Optional, Union
import aiofiles
import jinja2
import markdown
from utils.logging import get_logger
from utils.rendering.browser import browser_pool
from utils.paths import project_root

logger = get_logger("rendering")
TEMPLATES_PATH = str(Path(__file__).parent / "templates")


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


# ================= render_html =================


async def render_html(
    html: str,
    template_path: str = project_root().as_uri() + "/",
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
    full_page: Optional[bool] = True,
    **kwargs,
) -> bytes:
    """html 转图片，公共渲染入口。"""
    if "file:" not in template_path:
        raise Exception("template_path should be file:///path/to/template")
    async with browser_pool.page(
        device_scale_factor=device_scale_factor, **kwargs
    ) as page:
        page.on("console", lambda msg: logger.debug(f"[Browser Console]: {msg.text}"))
        await page.goto(template_path)
        await page.set_content(html, wait_until="load")
        await page.evaluate("document.fonts.ready")
        await page.evaluate(
            "Promise.all(Array.from(document.images).map(i => i.decode().catch(() => {})))"
        )
        return await page.screenshot(
            full_page=full_page,
            type=type,
            quality=quality,
            timeout=screenshot_timeout,
        )


# ================= render_template_image =================


async def render_template_image(
    template_path: str,
    template_name: str,
    templates: dict[Any, Any],
    filters: Optional[dict[str, Any]] = None,
    pages: Optional[dict[Any, Any]] = None,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """使用 jinja2 模板引擎通过 html 生成图片，公共渲染入口。"""
    if pages is None:
        pages = {
            "viewport": {"width": 500, "height": 10},
            "base_url": project_root().as_uri() + "/",
        }

    template_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path),
        enable_async=True,
    )

    if filters:
        for filter_name, filter_func in filters.items():
            template_env.filters[filter_name] = filter_func

    template = template_env.get_template(template_name)

    return await render_html(
        template_path=f"file://{template_path}",
        html=await template.render_async(**templates),
        type=type,
        quality=quality,
        device_scale_factor=device_scale_factor,
        screenshot_timeout=screenshot_timeout,
        **pages,
    )


# ================= render_markdown =================


async def render_markdown(
    md: str = "",
    md_path: str = "",
    css_path: str = "",
    width: int = 500,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """markdown 转图片，公共渲染入口。"""
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

    return await render_html(
        template_path=f"file://{css_path or TEMPLATES_PATH}",
        html=await template.render_async(md=md, css=css, extra=extra),
        viewport={"width": width, "height": 10},
        type=type,
        quality=quality,
        device_scale_factor=device_scale_factor,
        screenshot_timeout=screenshot_timeout,
    )


# ================= render_text =================


async def render_text(
    text: str,
    css_path: str = "",
    width: int = 500,
    type: Literal["jpeg", "png"] = "png",
    quality: Union[int, None] = None,
    device_scale_factor: float = 2,
    screenshot_timeout: Optional[float] = 30_000,
) -> bytes:
    """多行文本转图片，公共渲染入口。"""
    template = _env.get_template("text.html")

    return await render_html(
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

