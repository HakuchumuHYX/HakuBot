"""
赛事状态判定与日期解析
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytz

from ..data_manager import data_manager
from .constants import EVENT_STATE, UPCOMING_WINDOW_HOURS


def parse_mmdd(tz: pytz.BaseTzInfo, mmdd: str, end_of_day: bool) -> Optional[datetime]:
    """将 MM-DD 转为 tz-aware datetime（自动处理跨年）"""
    try:
        if not mmdd or "-" not in mmdd:
            return None
        month, day = map(int, mmdd.split("-"))
        now = datetime.now(tz)
        hour, minute, second = (23, 59, 59) if end_of_day else (0, 0, 0)

        dt = datetime(now.year, month, day, hour, minute, second, tzinfo=tz)

        # 如果日期比现在早很多（例如当前 01 月却解析到了上一年 12 月），则认为跨年
        if dt < now - timedelta(days=30):
            dt = datetime(now.year + 1, month, day, hour, minute, second, tzinfo=tz)
        return dt
    except Exception:
        return None


def get_event_state(tz: pytz.BaseTzInfo, end_grace_days: int, event_id: str) -> EVENT_STATE:
    sub = data_manager.get_any_subscription_by_event(event_id)
    if not sub or not sub.start_date or not sub.end_date:
        return "UNKNOWN"

    start_dt = parse_mmdd(tz, sub.start_date, end_of_day=False)
    end_dt = parse_mmdd(tz, sub.end_date, end_of_day=True)
    if not start_dt or not end_dt:
        return "UNKNOWN"

    now = datetime.now(tz)

    # ENDED：超过 end_dt + grace
    if now > end_dt + timedelta(days=end_grace_days):
        return "ENDED"

    # ONGOING：start_dt ~ end_dt + grace（包含赛事结束缓冲期，避免决赛跨日时状态空洞）
    if start_dt <= now <= end_dt + timedelta(days=end_grace_days):
        return "ONGOING"

    # UPCOMING：start_dt 前 UPCOMING_WINDOW_HOURS 进入窗口（恢复轮询）
    upcoming_start = start_dt - timedelta(hours=UPCOMING_WINDOW_HOURS)
    if upcoming_start <= now < start_dt:
        return "UPCOMING"

    # NOT_ONGOING：更早的未来阶段（保持 pause）
    if now < upcoming_start:
        return "NOT_ONGOING"

    return "UNKNOWN"


def has_active_events(tz: pytz.BaseTzInfo, end_grace_days: int) -> bool:
    """active = ONGOING 或 UPCOMING（窗口内才恢复轮询）"""
    event_ids = data_manager.get_all_subscribed_event_ids()
    for event_id in event_ids:
        if get_event_state(tz, end_grace_days, event_id) in ("ONGOING", "UPCOMING"):
            return True
    return False
