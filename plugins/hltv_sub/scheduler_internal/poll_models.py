"""
HLTVScheduler 核心类（多赛事独立 job 版本）
"""

from __future__ import annotations
import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, TypeVar
import pytz
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.log import logger
from utils.onebot.media import image_segment
from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.data_manager import data_manager
from plugins.hltv_sub.data_source import hltv_data
from plugins.hltv_sub.models import ResultInfo
from plugins.hltv_sub.render import render_reminder, render_stats
from plugins.hltv_sub.scheduler_internal.constants import (
    ADAPTIVE_INTERVAL_TABLE,
    AUTO_UNSUB_UNAVAILABLE_STREAK,
    DEFAULT_INTERVAL_MINUTES,
    OVERDUE_THRESHOLD_MINUTES,
    POST_LIVE_GRACE_MINUTES,
)
from plugins.hltv_sub.scheduler_internal.map_result_readiness import (
    build_completed_map_results,
)
from plugins.hltv_sub.scheduler_internal.result_readiness import (
    get_result_stats_push_block_reason,
)
from plugins.hltv_sub.scheduler_internal.state import get_event_state, parse_mmdd
from plugins.hltv_sub.scheduler_internal.types import CompletedMapResult, UpcomingMatch
from plugins.hltv_sub.scheduler_internal.wakeup import (
    refresh_wakeup_jobs as _refresh_wakeup_jobs,
)

T = TypeVar("T")


@dataclass
class EventPollState:
    current_interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    next_minutes_hint: Optional[int] = None
    has_live_match: bool = False
    last_live_seen_at: Optional[datetime] = None
    has_fetch_error: bool = False
    deterministic_unavailable_streak: int = 0
    last_unavailable_reason: str = ""
