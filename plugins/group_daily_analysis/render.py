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
        data = await download_avatar(user_id, size=140)
    except Exception as e:
        logger.warning(f"头像获取失败，不显示头像: user_id={user_id} {get_exc_desc(e)}")
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{MIME_TYPES[detect_format(data)]};base64,{encoded}"


async def render_report(result: AnalysisResult, group_name: str | None) -> bytes:
    stats = result.statistics
    qqs = list(
        {t.qq for t in result.user_titles if t.qq}
        | {q.qq for q in result.golden_quotes if q.qq}
    )
    avatars = dict(zip(qqs, await asyncio.gather(*map(_avatar, qqs))))
    now = datetime.now()

    # 场按开始时间排，场号就是先后顺序；没有时间的排最后
    topics = sorted(result.topics, key=lambda t: (not t.time, t.time))
    scenes_by_hour = {}
    for no, topic in enumerate(topics, 1):
        if topic.time:
            scenes_by_hour.setdefault(int(topic.time[:2]), []).append(no)
    peak = max(stats.hourly)
    most_lines = max((t.message_count for t in result.user_titles), default=0) or 1

    return await render_template(
        TEMPLATE_DIR,
        "report.html",
        width=800,
        date_text=f"{now.year}年{now.month}月{now.day}日",
        weekday="星期" + "一二三四五六日"[now.weekday()],
        group_name=group_name,
        headline=result.headline,
        message_count=stats.message_count,
        participant_count=stats.participant_count,
        total_characters=stats.total_characters,
        emoji_count=stats.emoji_count,
        hours=[
            {
                "hour": hour,
                "count": count,
                "height": max(2, count / peak * 100),
                "scenes": scenes_by_hour.get(hour, []),
            }
            for hour, count in enumerate(stats.hourly)
        ],
        peak_hour=stats.peak_hour,
        peak_count=peak,
        topics=topics,
        titles=result.user_titles,
        # 戏份条按人物表里发言最多的那个人算满
        most_lines=most_lines,
        quotes=sorted(result.golden_quotes, key=lambda q: (not q.time, q.time)),
        avatars=avatars,
        total_tokens=result.usage.total_tokens,
        watermark_text=plugin_config.watermark_text,
    )
