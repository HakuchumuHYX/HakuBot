from pathlib import Path
from typing import List

from nonebot import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from utils.rendering.engine import render_template
from plugins.pjsk_event_summary.models import EventSimple, EventDetail

TEMPLATE_DIR = Path(__file__).parent / "templates"
# 资源站有一张图卡住，页面 load 就等不到；先限时带图渲染，超时再出无图版
IMAGE_TIMEOUT = 15_000


async def _render(name: str, width: int, **ctx) -> bytes:
    try:
        return await render_template(
            TEMPLATE_DIR, name, width=width, timeout=IMAGE_TIMEOUT, with_images=True, **ctx
        )
    except PlaywrightTimeoutError:
        logger.warning(f"{name} 带远程图渲染超时，改出无图版")
        return await render_template(TEMPLATE_DIR, name, width=width, with_images=False, **ctx)


async def render_event_list_pic(
    events: List[EventSimple], watermark: str = "", global_watermark: str = ""
) -> bytes:
    return await _render(
        "list.html",
        850,
        events=events,
        watermark=watermark,
        global_watermark=global_watermark,
    )


async def render_event_detail_pic(
    data: EventDetail, watermark: str = "", global_watermark: str = ""
) -> bytes:
    return await _render(
        "detail.html",
        750,
        data=data,
        watermark=watermark,
        global_watermark=global_watermark,
    )
