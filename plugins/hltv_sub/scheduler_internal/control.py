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
from plugins.hltv_sub.scheduler_internal.poll_models import EventPollState


class ControlMixin:
    def _ensure_event_job(self, event_id: str) -> None:
        raise NotImplementedError

    def _pause_event_job(self, event_id: str) -> None:
        raise NotImplementedError

    def _resume_event_job(self, event_id: str) -> None:
        raise NotImplementedError

    def _remove_event_job(self, event_id: str) -> None:
        raise NotImplementedError

    def _reschedule_event_job_interval(self, event_id: str, minutes: int) -> None:
        raise NotImplementedError

    def _get_event_poll_state(self, event_id: str) -> EventPollState:
        if event_id not in self._event_states:
            self._event_states[event_id] = EventPollState()
        return self._event_states[event_id]

    def _cleanup_event_state_if_unsubscribed(self) -> None:
        subscribed = self.data_manager.get_all_subscribed_event_ids()
        stale = [eid for eid in self._event_states.keys() if eid not in subscribed]
        for eid in stale:
            self._event_states.pop(eid, None)
            self._event_run_locks.pop(eid, None)
            self._remove_event_job(eid)

    async def _on_wakeup(self, event_id: str) -> None:
        """start_dt - UPCOMING_WINDOW_HOURS 触发：恢复该 event job，并立即跑一轮"""
        logger.info(f"[HLTV Scheduler] 唤醒触发: event_id={event_id}")
        self.ensure_event_job_state(event_id)

        try:
            await self.run_check_for_event(event_id)
        except Exception as e:
            logger.warning(
                f"[HLTV Scheduler] 唤醒后立即检查失败 (event={event_id}): {e}"
            )

    def refresh_wakeup_jobs(self) -> None:
        _refresh_wakeup_jobs(self._tz, self._end_grace_days, self._on_wakeup)

    def ensure_event_job_state(self, event_id: str) -> None:
        """根据某赛事状态决定其 interval job 是否运行"""
        state = get_event_state(self._tz, self._end_grace_days, event_id)

        self._ensure_event_job(event_id)

        if state in ("ONGOING", "UPCOMING"):
            self._resume_event_job(event_id)
            self._reschedule_event_job_interval(event_id, DEFAULT_INTERVAL_MINUTES)
        else:
            self._pause_event_job(event_id)

    def ensure_job_state(self) -> None:
        """同步所有订阅赛事的 job 状态，并清理已取消订阅赛事的 job"""
        event_ids = self.data_manager.get_all_subscribed_event_ids()

        # 先确保每个订阅赛事 job 状态正确
        for event_id in event_ids:
            self.ensure_event_job_state(event_id)

        # 再移除取消订阅后残留的状态/job
        self._cleanup_event_state_if_unsubscribed()

    def _interval_from_next_minutes(self, next_minutes_until: Optional[int]) -> int:
        if next_minutes_until is None:
            return 360
        if next_minutes_until <= 0:
            return DEFAULT_INTERVAL_MINUTES
        for upper, interval in ADAPTIVE_INTERVAL_TABLE:
            if next_minutes_until <= upper:
                return interval
        return 360

    def _in_post_live_grace(self, poll_state: EventPollState) -> bool:
        if poll_state.last_live_seen_at is None:
            return False
        now = datetime.now(self._tz)
        elapsed = (now - poll_state.last_live_seen_at).total_seconds() / 60
        return elapsed <= POST_LIVE_GRACE_MINUTES

    def _is_deterministic_matches_unavailable(self, reason: str) -> bool:
        """是否属于可直接自动退订的 matches 不可用原因（避免临时网络问题误退订）"""
        return reason in {
            "generic_matches_page_no_event_matches",
            "http_404",
            "http_410",
        }

    def _update_unavailable_streak(
        self,
        event_id: str,
        *,
        is_unavailable: bool,
        reason: str,
    ) -> bool:
        poll_state = self._get_event_poll_state(event_id)

        if is_unavailable and self._is_deterministic_matches_unavailable(reason):
            if poll_state.last_unavailable_reason == reason:
                poll_state.deterministic_unavailable_streak += 1
            else:
                poll_state.deterministic_unavailable_streak = 1
                poll_state.last_unavailable_reason = reason

            logger.warning(
                f"[HLTV Scheduler] matches 不可用计数(event={event_id}): "
                f"reason={reason}, streak={poll_state.deterministic_unavailable_streak}/"
                f"{AUTO_UNSUB_UNAVAILABLE_STREAK}"
            )
            return (
                poll_state.deterministic_unavailable_streak
                >= AUTO_UNSUB_UNAVAILABLE_STREAK
            )

        if poll_state.deterministic_unavailable_streak > 0:
            logger.info(
                f"[HLTV Scheduler] matches 不可用计数已重置(event={event_id}): "
                f"last_reason={poll_state.last_unavailable_reason}, streak={poll_state.deterministic_unavailable_streak}"
            )
        poll_state.deterministic_unavailable_streak = 0
        poll_state.last_unavailable_reason = ""
        return False

    def _apply_adaptive_schedule(
        self, event_id: str, poll_state: EventPollState
    ) -> None:
        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state not in ("ONGOING", "UPCOMING"):
            return

        if poll_state.has_fetch_error:
            minutes = DEFAULT_INTERVAL_MINUTES
        elif poll_state.has_live_match:
            minutes = DEFAULT_INTERVAL_MINUTES
        elif self._in_post_live_grace(poll_state):
            minutes = DEFAULT_INTERVAL_MINUTES
        else:
            minutes = self._interval_from_next_minutes(poll_state.next_minutes_hint)

        logger.info(
            f"[HLTV Scheduler] 自适应轮询评估(event={event_id}): "
            f"next_minutes_until={poll_state.next_minutes_hint}, "
            f"has_live_match={poll_state.has_live_match}, "
            f"post_live_grace={self._in_post_live_grace(poll_state)}, "
            f"has_fetch_error={poll_state.has_fetch_error}, "
            f"target_interval={minutes}min, "
            f"current_interval={poll_state.current_interval_minutes}min"
        )

        self._reschedule_event_job_interval(event_id, minutes)

    def _parse_match_time(self, date_str: str, time_str: str) -> Optional[datetime]:
        """解析比赛时间（date: MM-DD, time: HH:MM）"""
        try:
            if not date_str or not time_str:
                return None

            if date_str == "LIVE" or time_str == "LIVE":
                return None

            now = datetime.now(self._tz)
            month, day = map(int, date_str.split("-"))
            hour, minute = map(int, time_str.split(":"))

            naive = datetime(now.year, month, day, hour, minute)
            match_time = self._tz.localize(naive)

            if match_time < now - timedelta(days=30):
                naive_next = datetime(now.year + 1, month, day, hour, minute)
                match_time = self._tz.localize(naive_next)

            return match_time
        except Exception:
            return None
