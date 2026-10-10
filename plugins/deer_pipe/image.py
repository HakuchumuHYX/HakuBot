"""deer_pipe 插件图片生成模块"""

import base64
from calendar import monthcalendar
from datetime import datetime

from plugins.deer_pipe.constants import TEMPLATE_DIR
from utils.images.formats import MIME_TYPES, detect_format
from utils.rendering.engine import render_template


async def render_calendar(
    now: datetime,
    deer_map: dict[int, int],
    avatar: bytes | None,
) -> bytes:
    """渲染签到日历，deer_map 为 {日期: 签到次数}"""
    avatar_uri = ""
    try:
        if avatar:
            mime = MIME_TYPES[detect_format(avatar)]
            avatar_uri = f"data:{mime};base64,{base64.b64encode(avatar).decode()}"
    except ValueError:
        # 头像来自适配器，格式不认识时就不画头像
        pass

    return await render_template(
        TEMPLATE_DIR,
        "calendar.html",
        width=784,
        year=now.year,
        month=now.month,
        today=now.day,
        weeks=monthcalendar(now.year, now.month),
        deer_map=deer_map,
        days_done=len(deer_map),
        total=sum(deer_map.values()),
        avatar=avatar_uri,
    )
