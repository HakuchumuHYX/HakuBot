"""
scheduler 类型定义（与业务无关的纯数据结构）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..models import MatchStats


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
    is_grand_final: bool = False
    is_third_place: bool = False


@dataclass
class CompletedMapResult:
    """A completed non-final map from a live BO3/BO5 match."""

    event_id: str
    event_title: str
    match_id: str
    team1: str
    team2: str
    bo_maps: int
    map_index: int
    map_name: str
    notification_id: str
    score1_after_map: str
    score2_after_map: str
    single_map_stats: MatchStats
