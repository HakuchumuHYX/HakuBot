"""每个赛事的轮询状态。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from plugins.hltv_sub.scheduler_internal.constants import DEFAULT_INTERVAL_MINUTES


@dataclass
class EventPollState:
    current_interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    next_minutes_hint: Optional[int] = None
    has_live_match: bool = False
    last_live_seen_at: Optional[datetime] = None
    has_fetch_error: bool = False
    deterministic_unavailable_streak: int = 0
    last_unavailable_reason: str = ""
