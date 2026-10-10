from pathlib import Path
from typing import List

from utils.rendering.engine import render_template
from plugins.pjsk_event_summary.models import EventSimple, EventDetail

TEMPLATE_DIR = Path(__file__).parent / "templates"


async def render_event_list_pic(
    events: List[EventSimple], watermark: str = "", global_watermark: str = ""
) -> bytes:
    return await render_template(
        TEMPLATE_DIR,
        "list.html",
        width=850,
        events=events,
        watermark=watermark,
        global_watermark=global_watermark,
    )


async def render_event_detail_pic(
    data: EventDetail, watermark: str = "", global_watermark: str = ""
) -> bytes:
    return await render_template(
        TEMPLATE_DIR,
        "detail.html",
        width=750,
        data=data,
        watermark=watermark,
        global_watermark=global_watermark,
    )
