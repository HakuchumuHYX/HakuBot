"""
scheduler 类型定义（与业务无关的纯数据结构）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UpcomingMatch:
    """即将开始的比赛信息"""

    match_id: str
    team1: str
    team2: str
    event_id: str
    event_title: str
    start_time: datetime
    minutes_until: int
    maps: str = ""
