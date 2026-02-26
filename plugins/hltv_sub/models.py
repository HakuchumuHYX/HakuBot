"""
HLTV 数据模型（dataclasses）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class EventInfo:
    """赛事信息"""
    id: str
    title: str
    start_date: str
    end_date: str
    prize: str = ""
    teams: str = ""
    location: str = ""
    is_ongoing: bool = False


@dataclass
class MatchInfo:
    """比赛信息（用于渲染/提醒：保持过滤 TBD 的语义）"""
    id: str
    date: str
    time: str
    team1: str
    team2: str
    team1_id: str
    team2_id: str
    maps: str = ""
    rating: int = 0
    event: str = ""
    is_live: bool = False


@dataclass
class MatchTimeHint:
    """比赛时间提示（用于 scheduler 自适应轮询；不依赖队伍是否已确定）

    说明：
    - 不过滤 TBD
    - 只提供 scheduler 需要的“时间/是否 LIVE/是否 TBD”等信息
    """
    match_id: str
    date: str
    time: str
    is_live: bool
    is_tbd: bool = False


@dataclass
class ResultInfo:
    """结果信息"""
    id: str
    date: str
    team1: str
    team2: str
    score1: str
    score2: str
    event: str = ""


@dataclass
class MapStats:
    """地图数据"""
    map_name: str
    pick_by: str  # team1, team2, or decider
    score_team1: str
    score_team2: str
    stats_id: str = ""  # 用于关联单图详细数据


@dataclass
class PlayerStats:
    """选手数据"""
    id: str
    nickname: str
    team: str  # team1 or team2
    kills: str
    deaths: str
    adr: str
    kast: str
    rating: str
    swing: str = ""


@dataclass
class MatchStats:
    """比赛详细数据"""
    match_id: str
    team1: str
    team2: str
    score1: str
    score2: str
    status: str
    maps: list[MapStats]
    players: list[PlayerStats]  # 总数据
    map_stats_details: Dict[str, List[PlayerStats]] = field(default_factory=dict)  # 单图详细数据 {map_name: players}
    vetos: list[str] = field(default_factory=list)
    event: str = ""
