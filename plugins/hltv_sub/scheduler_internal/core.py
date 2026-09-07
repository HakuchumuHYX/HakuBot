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
from plugins.hltv_sub.scheduler_internal.polling import PollingMixin
from plugins.hltv_sub.scheduler_internal.control import ControlMixin
from plugins.hltv_sub.scheduler_internal.maintenance import MaintenanceMixin
from plugins.hltv_sub.scheduler_internal.delivery import DeliveryMixin


class HLTVScheduler(PollingMixin, ControlMixin, MaintenanceMixin, DeliveryMixin):
    def __init__(self, *, manager=None, source=None):
        self.data_manager = manager if manager is not None else data_manager
        self.hltv_data = source if source is not None else hltv_data
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._initialized = False

        # 赛事结束判定缓冲（避免时区/页面延迟导致漏推最后结果）
        self._end_grace_days: int = max(
            0, int(plugin_config.hltv_auto_unsub_delay_days)
        )

        # 每个 event 的轮询状态
        self._event_states: dict[str, EventPollState] = {}
        self._event_run_locks: dict[str, asyncio.Lock] = {}

        # 抓取并发限制（避免多个赛事同时请求风暴）
        self._fetch_semaphore = asyncio.Semaphore(
            max(1, plugin_config.hltv_scheduler_max_parallel)
        )

    async def run_check_for_event(self, event_id: str) -> dict:
        lock = self._event_run_locks.setdefault(event_id, asyncio.Lock())
        async with lock:
            return await self._run_check_for_event_unlocked(event_id)

    async def _run_check_for_event_unlocked(self, event_id: str) -> dict:
        """执行某个赛事的一轮检查"""
        result: dict = {
            "event_id": event_id,
            "upcoming_matches": [],
            "completed_map_results": [],
            "new_results": [],
            "errors": [],
        }
        poll_state = self._get_event_poll_state(event_id)
        poll_state.has_fetch_error = False

        if event_id not in self.data_manager.get_all_subscribed_event_ids():
            return result

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state not in ("ONGOING", "UPCOMING"):
            self.ensure_event_job_state(event_id)
            return result

        try:
            try:
                bot = get_bot()
            except Exception:
                logger.debug(
                    f"[HLTV Scheduler] 无法获取 Bot，跳过推送 (event={event_id})"
                )
                return result

            upcoming = await self.check_match_starts_for_event(event_id)
            result["upcoming_matches"] = upcoming
            for match in upcoming:
                await self.send_match_reminder(bot, match)

            completed_maps = await self.check_completed_map_results_for_event(event_id)
            result["completed_map_results"] = [
                m.notification_id for m in completed_maps
            ]
            for completed_map in completed_maps:
                await self.send_completed_map_result(bot, completed_map)

            new_results = await self.check_match_results_for_event(event_id)
            result["new_results"] = [
                (eid, title, r.id) for eid, title, r in new_results
            ]
            for eid, title, r in new_results:
                await self.send_match_result(bot, eid, title, r)

            self._apply_adaptive_schedule(event_id, poll_state)

            logger.info(
                f"[HLTV Scheduler] 检查完成(event={event_id}): {len(upcoming)} 场即将开始, "
                f"{len(completed_maps)} 张单图结果, {len(new_results)} 场新结果"
            )
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查失败(event={event_id}): {e}")
            result["errors"].append(str(e))

        return result

    async def run_check(self) -> dict:
        """手动执行全量检查（调试命令/兼容旧接口）"""
        result: dict = {
            "upcoming_matches": [],
            "completed_map_results": [],
            "new_results": [],
            "errors": [],
        }
        event_ids = sorted(self.data_manager.get_all_subscribed_event_ids())

        for event_id in event_ids:
            r = await self.run_check_for_event(event_id)
            result["upcoming_matches"].extend(r.get("upcoming_matches", []))
            result["completed_map_results"].extend(r.get("completed_map_results", []))
            result["new_results"].extend(r.get("new_results", []))
            result["errors"].extend(r.get("errors", []))

        return result

    async def get_upcoming_info(self) -> list[UpcomingMatch]:
        """获取所有即将开始的比赛信息（用于测试命令）"""
        upcoming: list[UpcomingMatch] = []
        now = datetime.now(self._tz)

        event_ids = self.data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            return upcoming

        for event_id in event_ids:
            if get_event_state(self._tz, self._end_grace_days, event_id) == "ENDED":
                continue

            sub = self.data_manager.get_any_subscription_by_event(event_id)
            event_title = sub.event_title if sub else f"Event #{event_id}"

            try:
                matches = await self.hltv_data.get_event_matches(event_id)
                for match in matches:
                    if match.is_live:
                        continue

                    match_time = self._parse_match_time(match.date, match.time)
                    if not match_time:
                        continue

                    seconds_until = (match_time - now).total_seconds()
                    if seconds_until > 0:
                        upcoming.append(
                            UpcomingMatch(
                                match_id=match.id,
                                team1=match.team1,
                                team2=match.team2,
                                event_id=event_id,
                                event_title=event_title,
                                start_time=match_time,
                                minutes_until=int(math.ceil(seconds_until / 60)),
                                maps=match.maps,
                                is_grand_final=match.is_grand_final,
                                is_third_place=match.is_third_place,
                            )
                        )
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 获取赛事 {event_id} 比赛失败: {e}")
                continue

        upcoming.sort(key=lambda x: x.start_time)
        return upcoming
