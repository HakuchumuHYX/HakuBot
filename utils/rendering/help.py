"""A single help renderer using HakuBot's existing light-blue and night cards."""

from dataclasses import asdict
import asyncio
import hashlib
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .browser import browser_pool
from .cache import cached_render
from .fonts import font_css, font_fingerprint
from .models import HelpDocument, RenderError, RenderedDocument, RenderedPage
from .themes import resolve_theme

_templates = Path(__file__).parent / "templates"


def help_to_text(document: HelpDocument) -> str:
    blocks = [document.title]
    if document.introduction:
        blocks.append(document.introduction)
    blocks.extend(p.text for p in document.paragraphs)
    for section in document.sections:
        blocks.append(
            f"【{section.title}】" + (f"\n{section.note}" if section.note else "")
        )
        for entry in section.entries:
            text = " ".join(p for p in (entry.command, entry.arguments) if p)
            if entry.permission:
                text += f" [{entry.permission}]"
            if entry.description:
                text += "\n" + entry.description
            if entry.aliases:
                text += "\n别名：" + "、".join(entry.aliases)
            if entry.example:
                text += "\n示例：" + entry.example
            blocks.append(text)
    blocks.extend(document.tips)
    blocks.extend(document.links)
    if document.footer:
        blocks.append(document.footer)
    return "\n\n".join(blocks)


def _chunks(text, length=600):
    return [text[i : i + length] for i in range(0, len(text), length)] or [""]


def _units(document):
    result = []
    for paragraph in document.paragraphs:
        result.extend({"title": "", "text": part} for part in _chunks(paragraph.text))
    for section in document.sections:
        result.append({"title": section.title, "text": section.note})
        for entry in section.entries:
            title = " ".join(filter(None, (entry.command, entry.arguments)))
            if entry.permission:
                title += f" [{entry.permission}]"
            text = entry.description
            if entry.aliases:
                text += "\n别名：" + "、".join(entry.aliases)
            if entry.example:
                text += "\n示例：" + entry.example
            for i, part in enumerate(_chunks(text)):
                result.append({"title": title + ("（续）" if i else ""), "text": part})
    for tip in (*document.tips, *document.links):
        result.extend({"title": "", "text": part} for part in _chunks(tip))
    return result


async def render_help(
    document: HelpDocument, *, theme="auto", force=False
) -> RenderedDocument:
    selected = resolve_theme(theme)  # Resolve once, including for multi-page documents.
    try:
        template_bytes = (_templates / "help.html").read_bytes()
        material = json.dumps(
            {
                "document": asdict(document),
                "theme": asdict(selected),
                "template": hashlib.sha256(template_bytes).hexdigest(),
                "fonts": await asyncio.to_thread(font_fingerprint),
                "width": 800,
                "scale": 2,
                "max_height": 2400,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        key = hashlib.sha256(material.encode()).hexdigest()

        async def render():
            env = Environment(
                loader=FileSystemLoader(_templates), autoescape=select_autoescape()
            )
            html = env.get_template("help.html").render(
                document=document,
                units=_units(document),
                theme_css=selected.css(),
                font_css=font_css(),
            )
            async with browser_pool.page(
                viewport={"width": 800, "height": 100}
            ) as page:
                await page.goto(_templates.as_uri() + "/")
                await page.set_content(html, wait_until="load")
                await page.evaluate("document.fonts.ready")
                await page.evaluate("window.paginateHelp()")
                sheets = page.locator(".sheet")
                pages = []
                for i in range(await sheets.count()):
                    sheet = sheets.nth(i)
                    box = await sheet.bounding_box()
                    if not box or box["height"] > 2400:
                        raise RenderError("Help page exceeds height limit")
                    data = await sheet.screenshot(type="png")
                    pages.append(
                        RenderedPage(
                            data, round(box["width"] * 2), round(box["height"] * 2)
                        )
                    )
                return RenderedDocument(tuple(pages))

        return await cached_render(key, render, force=force)
    except RenderError:
        raise
    except Exception as exc:
        raise RenderError(f"Help rendering failed: {type(exc).__name__}") from exc
