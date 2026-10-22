"""日报长图"""

import asyncio
import base64
from datetime import datetime
from pathlib import Path

from nonebot import logger

from utils.images.formats import MIME_TYPES, detect_format
from utils.logging import get_exc_desc
from utils.onebot.avatar import download_avatar
from utils.rendering.engine import render_template
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import AnalysisResult

TEMPLATE_DIR = Path(__file__).parent / "templates"


async def _avatar(user_id: int) -> str | None:
    try:
        data = await download_avatar(user_id, size=100)
    except Exception as e:
        logger.warning(f"头像获取失败，不显示头像: user_id={user_id} {get_exc_desc(e)}")
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{MIME_TYPES[detect_format(data)]};base64,{encoded}"


async def render_report(result: AnalysisResult) -> bytes:
    stats = result.statistics
    qqs = list(
        {t.qq for t in result.user_titles if t.qq}
        | {q.qq for q in result.golden_quotes if q.qq}
    )
    avatars = dict(zip(qqs, await asyncio.gather(*map(_avatar, qqs))))
    peak = max(stats.hourly) or 1
    now = datetime.now()

    return await render_template(
        TEMPLATE_DIR,
        "image_template.html",
        width=800,
        current_date=now.strftime("%Y年%m月%d日"),
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S"),
        message_count=stats.message_count,
        participant_count=stats.participant_count,
        total_characters=stats.total_characters,
        emoji_count=stats.emoji_count,
        most_active_period=stats.most_active_period,
        chart_data=[
            {"hour": hour, "count": count, "percentage": int(count / peak * 100)}
            for hour, count in enumerate(stats.hourly)
        ],
        topics=[
            {
                "index": i,
                "title": t.topic,
                "detail": t.detail,
                "contributors": "、".join(t.contributors),
            }
            for i, t in enumerate(result.topics, 1)
        ],
        titles=[
            {
                "name": t.name,
                "title": t.title,
                "reason": t.reason,
                "personality": t.personality,
                "avatar_data": avatars.get(t.qq),
            }
            for t in result.user_titles
        ],
        quotes=[
            {
                "content": q.content,
                "sender": q.sender,
                "reason": q.reason,
                "avatar_url": avatars.get(q.qq),
            }
            for q in result.golden_quotes
        ],
        total_tokens=result.usage.total_tokens,
        prompt_tokens=result.usage.prompt_tokens,
        completion_tokens=result.usage.completion_tokens,
        watermark_text=plugin_config.watermark_text,
    )
