from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple, Optional

from plugins.group_statistics.utils import get_additional_text
from utils.rendering.engine import render_template

TZ_CN = timezone(timedelta(hours=8))
TEMPLATE_DIR = Path(__file__).parent / "templates"


def _fmt_date(d: date) -> str:
    return d.strftime("%Y.%m.%d")


async def render_today_stat_image(
    total: int, top_users: List[Tuple[str, int]], stat_date: Optional[date] = None
) -> bytes:
    """渲染“今日统计”图片（不含每日额外文案）

    Args:
        stat_date: 统计日期（默认取今天）
    """
    if stat_date is None:
        stat_date = datetime.now(TZ_CN).date()

    return await render_template(
        TEMPLATE_DIR,
        "leaderboard.html",
        width=720,
        title="今日消息统计",
        date=_fmt_date(stat_date),
        total=total,
        rows=top_users,
    )


async def render_daily_stat_image(
    total: int, top_users: List[Tuple[str, int]], stat_date: date
) -> bytes:
    """渲染“每日统计”图片（包含阈值额外文案）

    注意：stat_date 是“被统计的那一天”，不等于消息发送日期。
    例如 2026.02.07 00:00 推送的统计，stat_date 应为 2026.02.06。
    """
    return await render_template(
        TEMPLATE_DIR,
        "leaderboard.html",
        width=720,
        title="每日消息统计",
        date=_fmt_date(stat_date),
        total=total,
        rows=top_users,
        extra=get_additional_text(total),
    )
