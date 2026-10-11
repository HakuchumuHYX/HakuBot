"""
HLTV 数据模型（dataclasses）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List


@dataclass
class MvpStat:
    """value 是相对赛事平均水平的倍数，1.0 即平均；label 是页面展示值。"""

    name: str
    label: str
    value: float


@dataclass
class EventMvp:
    nickname: str
    first_name: str
    last_name: str
    country: str
    country_name: str
    photo: str
    medal: str
    stats: list[MvpStat] = field(default_factory=list)


@dataclass
class EventInfo:
    """赛事日期使用 YYYY-MM-DD，展示时再格式化。"""

    id: str
    title: str
    start_date: str
    end_date: str
    prize: str = ""
    teams: str = ""
    location: str = ""
    country: str = ""  # 举办地旗帜代码，只有即将举行的大型赛事有
    logo: str = ""  # 只有正在进行的赛事卡片带 logo
    is_ongoing: bool = False
    mvp: EventMvp | None = None  # 只有详情页在赛后评出 MVP 时有


@dataclass
class MatchInfo:
    """比赛信息（用于渲染/提醒：保持过滤 TBD 的语义）"""

    id: str
    start_time: datetime | None
    team1: str
    team2: str
    team1_id: str
    team2_id: str
    team1_logo: str = ""
    team2_logo: str = ""
    maps: str = ""
    rating: int = 0
    event: str = ""
    is_live: bool = False
    is_grand_final: bool = False
    is_third_place: bool = False


@dataclass
class MatchTimeHint:
    """比赛时间提示（用于 scheduler 自适应轮询；不依赖队伍是否已确定）

    说明：
    - 不过滤 TBD
    - 保留原始开赛时间与 LIVE/TBD 状态，不使用展示字符串做调度
    """

    match_id: str
    start_time: datetime | None
    is_live: bool
    is_tbd: bool = False


@dataclass
class ResultInfo:
    """结果信息；date 为 YYYY-MM-DD，format 为 bo3/bo5 或 bo1 的地图名。"""

    id: str
    date: str
    team1: str
    team2: str
    score1: str
    score2: str
    team1_logo: str = ""
    team2_logo: str = ""
    format: str = ""
    event: str = ""


@dataclass
class HalfScore:
    """半场比分；team1_side 为 team1 在该半场的阵营（ct / t），加时可能为空。"""

    score1: str
    score2: str
    team1_side: str = ""


@dataclass
class MapStats:
    """地图数据"""

    map_name: str
    pick_by: str  # team1 / team2；空串表示 decider
    score_team1: str
    score_team2: str
    stats_id: str = ""  # 用于关联单图详细数据
    halves: list[HalfScore] = field(default_factory=list)
    image: str = ""  # HLTV 的地图横幅


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
    country: str = ""  # HLTV 旗帜代码，如 SE、EU、WORLD
    country_name: str = ""


@dataclass
class VrsChange:
    """赛后 VRS result：HLTV 只按本场胜负估算，正式 VRS 还会计入名次、奖金等。"""

    points_before: int
    rank_before: int
    points_delta: int
    rank_after: int


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
    map_stats_details: Dict[str, List[PlayerStats]] = field(
        default_factory=dict
    )  # 单图详细数据 {map_name: players}
    vetos: list[str] = field(default_factory=list)
    event: str = ""
    team1_logo: str = ""
    team2_logo: str = ""
    team1_country: str = ""
    team1_country_name: str = ""
    team2_country: str = ""
    team2_country_name: str = ""
    format_line: str = ""  # 如 "Best of 3 (LAN)"
    stage: str = ""  # 如 "Semi-final"
    is_grand_final: bool = False
    date: str = ""  # YYYY-MM-DD
    team1_vrs: VrsChange | None = None  # 仅比赛结束后有
    team2_vrs: VrsChange | None = None


@dataclass
class EventMatchesMeta:
    final_url: str = ""
    page_title: str = ""
    match_wrapper_count: int = 0
    is_unavailable: bool = False
    unavailable_reason: str = ""


@dataclass
class EventMatchesPage:
    matches: list[MatchInfo]
    hints: list[MatchTimeHint]
    meta: EventMatchesMeta


@dataclass
class UpcomingMatch:
    """即将开始的比赛信息"""

    match_id: str
    team1: str
    team2: str
    event_id: str
    event_title: str
    start_time: datetime
    maps: str = ""
    is_grand_final: bool = False
    is_third_place: bool = False


@dataclass
class EventCheckResult:
    event_id: str
    start_candidates: int = 0
    map_candidates: int = 0
    result_candidates: int = 0
    errors: list[str] = field(default_factory=list)
    next_minutes_hint: int | None = None
    has_live_match: bool = False
    live_seen_at: datetime | None = None
    has_error: bool = False
    retry_at: float = 0
