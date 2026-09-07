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


class PollingMixin:
    async def _fetch_with_retry(
        self,
        coro_func: Callable[[], T],
        max_retries: int = 3,
        delay: float = 2.0,
        event_id: str = "",
    ) -> Optional[T]:
        """带重试的异步请求（受并发信号量控制）"""
        for attempt in range(max_retries):
            try:
                async with self._fetch_semaphore:
                    return await coro_func()
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"[HLTV Scheduler] 请求失败 (event={event_id}, 尝试 {attempt + 1}/{max_retries}): {e}"
                    )
                    state = self._get_event_poll_state(event_id)
                    state.has_fetch_error = True
                    return None
                logger.warning(
                    f"[HLTV Scheduler] 请求失败 (event={event_id}, 尝试 {attempt + 1}/{max_retries}): {e}，{delay * (attempt + 1)}秒后重试"
                )
                await asyncio.sleep(delay * (attempt + 1))
        return None

    async def check_match_starts_for_event(self, event_id: str) -> list[UpcomingMatch]:
        upcoming: list[UpcomingMatch] = []
        now = datetime.now(self._tz)

        poll_state = self._get_event_poll_state(event_id)
        poll_state.has_live_match = False
        poll_state.next_minutes_hint = None

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state in ("ENDED", "NOT_ONGOING", "UNKNOWN"):
            logger.info(f"[HLTV Scheduler] 跳过赛事 {event_id}: state={state}")
            return upcoming

        sub = self.data_manager.get_any_subscription_by_event(event_id)
        event_title = sub.event_title if sub else f"Event #{event_id}"

        try:
            triplet = await self._fetch_with_retry(
                lambda eid=event_id: (
                    self.hltv_data.get_event_matches_with_hints_and_meta(eid)
                ),
                event_id=event_id,
            )
            if not triplet:
                return upcoming

            matches, hints, meta = triplet

            self._update_unavailable_streak(
                event_id,
                is_unavailable=meta.is_unavailable,
                reason=meta.unavailable_reason,
            )
            if meta.is_unavailable and self._is_deterministic_matches_unavailable(
                meta.unavailable_reason
            ):
                logger.warning(
                    f"[HLTV Scheduler] 延迟自动退订赛事 {event_id}: "
                    f"state={state} 时 matches 页面不可用(reason={meta.unavailable_reason})"
                )
                return upcoming

            if meta.is_unavailable:
                return upcoming

            if any(m.is_live for m in matches) or any(h.is_live for h in hints):
                poll_state.has_live_match = True
                poll_state.last_live_seen_at = datetime.now(self._tz)

            logger.info(
                f"[HLTV Scheduler] 赛事 {event_id} matches抓取: filtered={len(matches)}, hints={len(hints)}"
            )

            hint_by_id = {h.match_id: h for h in hints}

            local_next: Optional[int] = None
            for h in hints:
                if h.is_live:
                    continue

                match_time = self._parse_match_time(h.date, h.time)
                if not match_time:
                    if not h.is_tbd:
                        local_next = 0 if local_next is None else min(local_next, 0)
                    continue

                seconds_until = (match_time - now).total_seconds()
                if seconds_until > 0:
                    minutes_until = int(seconds_until // 60)
                    local_next = (
                        minutes_until
                        if local_next is None
                        else min(local_next, minutes_until)
                    )
                else:
                    elapsed_minutes = abs(seconds_until) / 60
                    if elapsed_minutes <= OVERDUE_THRESHOLD_MINUTES:
                        local_next = 0 if local_next is None else min(local_next, 0)

            poll_state.next_minutes_hint = local_next

            if not matches:
                return upcoming

            for match in matches:
                if self.data_manager.is_start_notified(match.id):
                    continue

                should_remind = False
                remind_reason = ""

                if match.is_live:
                    should_remind = True
                    remind_reason = "stage3_live"
                else:
                    h = hint_by_id.get(match.id)
                    if h and (not h.is_live) and (not h.is_tbd):
                        match_time = self._parse_match_time(h.date, h.time)
                        if match_time is None:
                            should_remind = True
                            remind_reason = "stage2_no_time"
                        else:
                            elapsed = (now - match_time).total_seconds()
                            if 0 < elapsed <= OVERDUE_THRESHOLD_MINUTES * 60:
                                should_remind = True
                                remind_reason = "stage1_overdue"

                if not should_remind:
                    continue

                logger.info(
                    f"[HLTV Scheduler] 开赛提醒触发: match_id={match.id}, event={event_id}, reason={remind_reason}"
                )
                upcoming.append(
                    UpcomingMatch(
                        match_id=match.id,
                        team1=match.team1,
                        team2=match.team2,
                        event_id=event_id,
                        event_title=event_title,
                        start_time=now,
                        minutes_until=0,
                        maps=match.maps,
                        is_grand_final=match.is_grand_final,
                        is_third_place=match.is_third_place,
                    )
                )

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 比赛失败: {e}")
            poll_state.has_fetch_error = True

        return upcoming

    async def check_match_results_for_event(
        self, event_id: str
    ) -> list[tuple[str, str, ResultInfo]]:
        new_results: list[tuple[str, str, ResultInfo]] = []

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        # 边界补抓：UPCOMING 窗口内也允许拉取结果，避免状态切换边缘漏推
        if state not in ("ONGOING", "UPCOMING"):
            return new_results

        sub = self.data_manager.get_any_subscription_by_event(event_id)
        event_title = sub.event_title if sub else f"Event #{event_id}"

        poll_state = self._get_event_poll_state(event_id)

        try:
            results = await self._fetch_with_retry(
                lambda eid=event_id: self.hltv_data.get_event_results(
                    eid, max_results=5
                ),
                event_id=event_id,
            )
            if not results:
                return new_results

            for r in results:
                if not self.data_manager.is_result_notified(r.id):
                    new_results.append((event_id, event_title, r))
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 结果失败: {e}")
            poll_state.has_fetch_error = True

        return new_results

    async def check_completed_map_results_for_event(
        self, event_id: str
    ) -> list[CompletedMapResult]:
        completed_maps: list[CompletedMapResult] = []

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state not in ("ONGOING", "UPCOMING"):
            return completed_maps

        sub = self.data_manager.get_any_subscription_by_event(event_id)
        event_title = sub.event_title if sub else f"Event #{event_id}"
        poll_state = self._get_event_poll_state(event_id)

        try:
            triplet = await self._fetch_with_retry(
                lambda eid=event_id: (
                    self.hltv_data.get_event_matches_with_hints_and_meta(eid)
                ),
                event_id=event_id,
            )
            if not triplet:
                return completed_maps

            matches, hints, meta = triplet
            if meta.is_unavailable:
                return completed_maps

            if any(m.is_live for m in matches) or any(h.is_live for h in hints):
                poll_state.has_live_match = True
                poll_state.last_live_seen_at = datetime.now(self._tz)

            for match in matches:
                if not match.is_live:
                    continue
                if match.maps not in {"3", "5"}:
                    continue

                bo_maps = int(match.maps)
                stats = await self._fetch_with_retry(
                    lambda m=match: self.hltv_data.get_match_stats(
                        match_id=m.id,
                        team1=m.team1,
                        team2=m.team2,
                        event_title=event_title,
                    ),
                    event_id=event_id,
                )
                for candidate in build_completed_map_results(
                    event_id=event_id,
                    event_title=event_title,
                    match_id=match.id,
                    team1=match.team1,
                    team2=match.team2,
                    bo_maps=bo_maps,
                    stats=stats,
                ):
                    if not self.data_manager.is_map_result_notified(
                        candidate.notification_id
                    ):
                        completed_maps.append(candidate)

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 单图结果失败: {e}")
            poll_state.has_fetch_error = True

        return completed_maps
