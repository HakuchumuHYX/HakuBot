"""把解析结果渲染成卡片图（templates/card.html）"""

from pathlib import Path

from nonebot import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from plugins.analysis_bilibili.parser import Card
from utils.logging import get_exc_desc
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"
# 超时多半是远程图片卡住，留够重渲染无图版的时间
RENDER_TIMEOUT = 15_000


async def _render(card: Card, *, with_images: bool) -> bytes:
    return await render_template(
        TEMPLATE_DIR,
        "card.html",
        width=500,
        type="jpeg",
        quality=90,
        timeout=RENDER_TIMEOUT,
        kind=card.kind,
        d=card.data,
        with_images=with_images,
    )


async def render_card(card: Card) -> bytes:
    """远程图片拖到超时时，重渲染一版不带图的"""
    try:
        return await _render(card, with_images=True)
    except PlaywrightTimeoutError as exc:
        logger.warning(f"卡片渲染超时，改用无图版: {get_exc_desc(exc)}")
        return await _render(card, with_images=False)
