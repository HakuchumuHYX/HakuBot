"""一个周期扫描任务，按赛事的 next_check_at 控制实际抓取频率。"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pytz
from nonebot import require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.data_manager import data_manager
from plugins.hltv_sub.handler import UPCOMING_WINDOW_HOURS, get_event_state, hltv_handler
from plugins.hltv_sub.models import EventCheckResult

DEFAULT_INTERVAL_MINUTES = 3
ADAPTIVE_INTERVAL_TABLE = [(60, 3), (6 * 60, 30), (24 * 60, 180)]
POST_LIVE_GRACE_MINUTES = 30


@dataclass
class _Schedule:
    next_check_at: datetime
    last_live_seen_at: datetime | None = None


class HLTVScheduler:
    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._end_grace_days = max(0, plugin_config.hltv_auto_unsub_delay_days)
        self._events: dict[str, _Schedule] = {}
        self._had_receivers = False

    def start(self) -> None:
        self.sync_events()
        self._had_receivers = data_manager.has_enabled_groups()
        scheduler.add_job(
            self.check_due_events, "interval", minutes=1,
            id="hltv_check_due", replace_existing=True,
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            self.daily_maintenance, "cron", hour=4, minute=30,
            id="hltv_daily_maintenance", replace_existing=True,
            max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            self.auto_subscribe_big_events, "interval",
            days=plugin_config.hltv_auto_sub_interval_days,
            id="hltv_auto_subscribe", replace_existing=True,
            max_instances=1, coalesce=True,
        )
        logger.info("[HLTV] 周期检查、每日维护和自动订阅任务已启动")

    def sync_events(self, *, wake: bool = False) -> None:
        subscribed = data_manager.get_all_subscribed_event_ids()
        now = datetime.now(self._tz)
        for event_id in self._events.keys() - subscribed:
            self._events.pop(event_id)
        for event_id in subscribed:
            if event_id not in self._events:
                self._events[event_id] = _Schedule(now)
            elif wake:
                self._events[event_id].next_check_at = now
        hltv_handler.cleanup_unsubscribed()

    async def check_due_events(self) -> None:
        if not data_manager.has_enabled_groups():
            self._had_receivers = False
            return
        if not self._had_receivers:
            # 从全部关闭恢复时补一轮自动订阅，不必等七天心跳。
            self._had_receivers = True
            await self.auto_subscribe_big_events()
        self.sync_events()
        for event_id in sorted(self._events):
            schedule = self._events.get(event_id)
            if schedule is not None and schedule.next_check_at <= datetime.now(self._tz):
                await self.run_check_for_event(event_id)

    async def run_check_for_event(self, event_id: str) -> EventCheckResult:
        result = EventCheckResult(event_id)
        sub = data_manager.get_subscription(event_id)
        if sub is None or not data_manager.has_enabled_groups():
            return result
        now = datetime.now(self._tz)
        schedule = self._events.setdefault(event_id, _Schedule(now))
        state = get_event_state(sub, now, self._end_grace_days)
        if state == "NOT_ONGOING":
            start_at = self._tz.localize(datetime.combine(
                date.fromisoformat(sub.start_date), time.min,
            ))
            schedule.next_check_at = start_at - timedelta(hours=UPCOMING_WINDOW_HOURS)
            return result
        if state not in ("ONGOING", "UPCOMING"):
            schedule.next_check_at = now + timedelta(days=1)
            if state == "UNKNOWN":
                logger.warning(f"[HLTV] 赛事 {event_id} 日期无效，等待维护补全")
            return result

        result = await hltv_handler.run_check_for_event(event_id)
        if not data_manager.is_subscribed(event_id):
            self.sync_events()
            return result

        now = datetime.now(self._tz)
        if result.live_seen_at is not None:
            schedule.last_live_seen_at = result.live_seen_at
        in_grace = (
            schedule.last_live_seen_at is not None
            and now - schedule.last_live_seen_at <= timedelta(minutes=POST_LIVE_GRACE_MINUTES)
        )
        if result.has_error or result.has_live_match or in_grace:
            minutes = DEFAULT_INTERVAL_MINUTES
        elif result.next_minutes_hint is None:
            minutes = 360
        else:
            minutes = next(
                (
                    interval for upper, interval in ADAPTIVE_INTERVAL_TABLE
                    if result.next_minutes_hint <= upper
                ),
                360,
            )
        jitter = random.randint(0, max(0, plugin_config.hltv_scheduler_jitter_seconds))
        schedule.next_check_at = now + timedelta(minutes=minutes, seconds=jitter)
        if result.retry_at:
            schedule.next_check_at = max(
                schedule.next_check_at, datetime.fromtimestamp(result.retry_at, self._tz),
            )
        logger.debug(f"[HLTV] 赛事 {event_id} 下次检查：{schedule.next_check_at.isoformat()}")
        return result

    async def run_check(self) -> dict:
        """手动触发忽略 next_check_at，但仍经过业务锁、节流和冷却。"""
        self.sync_events()
        summary = {
            "start_candidates": 0, "map_candidates": 0,
            "result_candidates": 0, "errors": [],
        }
        for event_id in sorted(data_manager.get_all_subscribed_event_ids()):
            result = await self.run_check_for_event(event_id)
            summary["start_candidates"] += result.start_candidates
            summary["map_candidates"] += result.map_candidates
            summary["result_candidates"] += result.result_candidates
            summary["errors"].extend(result.errors)
        return summary

    async def daily_maintenance(self) -> None:
        try:
            await hltv_handler.daily_maintenance()
        finally:
            self.sync_events(wake=True)

    async def auto_subscribe_big_events(self) -> list[str]:
        try:
            return await hltv_handler.auto_subscribe_big_events()
        finally:
            self.sync_events()


hltv_scheduler = HLTVScheduler()
