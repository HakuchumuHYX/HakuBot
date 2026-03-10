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

from ..config import plugin_config
from ..data_manager import data_manager
from ..data_source import hltv_data
from ..models import ResultInfo
from ..render import render_reminder, render_stats
from .constants import (
    ADAPTIVE_INTERVAL_TABLE,
    DEFAULT_INTERVAL_MINUTES,
    OVERDUE_THRESHOLD_MINUTES,
    POST_LIVE_GRACE_MINUTES,
)
from .state import get_event_state, parse_mmdd
from .types import UpcomingMatch
from .wakeup import refresh_wakeup_jobs as _refresh_wakeup_jobs

T = TypeVar("T")


@dataclass
class EventPollState:
    current_interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    next_minutes_hint: Optional[int] = None
    has_live_match: bool = False
    last_live_seen_at: Optional[datetime] = None
    has_fetch_error: bool = False


class HLTVScheduler:
    """HLTV 定时任务调度器（每个 event 独立 interval job）"""

    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._initialized = False

        # 赛事结束判定缓冲（避免时区/页面延迟导致漏推最后结果）
        self._end_grace_days: int = 1

        # 每个 event 的轮询状态
        self._event_states: dict[str, EventPollState] = {}

        # 抓取并发限制（避免多个赛事同时请求风暴）
        self._fetch_semaphore = asyncio.Semaphore(max(1, plugin_config.hltv_scheduler_max_parallel))

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

    # -------------------- Job 控制（由 bootstrap 注入） --------------------

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

    # -------------------- 状态辅助 --------------------

    def _get_event_poll_state(self, event_id: str) -> EventPollState:
        if event_id not in self._event_states:
            self._event_states[event_id] = EventPollState()
        return self._event_states[event_id]

    def _cleanup_event_state_if_unsubscribed(self) -> None:
        subscribed = data_manager.get_all_subscribed_event_ids()
        stale = [eid for eid in self._event_states.keys() if eid not in subscribed]
        for eid in stale:
            self._event_states.pop(eid, None)
            self._remove_event_job(eid)

    # -------------------- Wakeup 触发器（date job） --------------------

    async def _on_wakeup(self, event_id: str) -> None:
        """start_dt - UPCOMING_WINDOW_HOURS 触发：恢复该 event job，并立即跑一轮"""
        logger.info(f"[HLTV Scheduler] 唤醒触发: event_id={event_id}")
        self.ensure_event_job_state(event_id)

        try:
            await self.run_check_for_event(event_id)
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 唤醒后立即检查失败 (event={event_id}): {e}")

    def refresh_wakeup_jobs(self) -> None:
        _refresh_wakeup_jobs(self._tz, self._end_grace_days, self._on_wakeup)

    # -------------------- 订阅状态 -> job 状态 --------------------

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
        event_ids = data_manager.get_all_subscribed_event_ids()

        # 先确保每个订阅赛事 job 状态正确
        for event_id in event_ids:
            self.ensure_event_job_state(event_id)

        # 再移除取消订阅后残留的状态/job
        self._cleanup_event_state_if_unsubscribed()

    # -------------------- 自适应轮询 --------------------

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

    def _apply_adaptive_schedule(self, event_id: str, poll_state: EventPollState) -> None:
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

    # -------------------- 初始化（基线 results 标记） --------------------

    async def init_existing_results(self) -> int:
        """启动时初始化：将现有结果标记为已推送，避免重启后误推送"""
        if self._initialized:
            return 0

        event_ids = data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            self._initialized = True
            return 0

        count = 0
        for event_id in event_ids:
            try:
                results = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_results(eid, max_results=10),
                    event_id=event_id,
                )
                if results:
                    for r in results:
                        if not data_manager.is_result_notified(r.id):
                            data_manager.add_notified_result(r.id)
                            count += 1
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 初始化赛事 {event_id} 结果失败: {e}")
                continue

        self._initialized = True
        logger.info(f"[HLTV Scheduler] 已初始化 {count} 条历史结果记录")
        return count

    async def initialize_event_results_as_notified(
        self, event_id: str, max_results: int = 10
    ) -> int:
        """订阅进行中赛事时调用：把当前已有结果先标记为已推送，避免订阅后立刻推历史结果"""
        try:
            results = await self._fetch_with_retry(
                lambda eid=event_id: hltv_data.get_event_results(eid, max_results=max_results),
                event_id=event_id,
            )
            if not results:
                return 0

            count = 0
            for r in results:
                if not data_manager.is_result_notified(r.id):
                    data_manager.add_notified_result(r.id)
                    count += 1
            logger.info(
                f"[HLTV Scheduler] 订阅初始化：已标记 {count} 条现有结果为已推送 (event {event_id})"
            )
            return count
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 订阅初始化失败 (event {event_id}): {e}")
            return 0

    # -------------------- 核心检查逻辑 --------------------

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

        sub = data_manager.get_any_subscription_by_event(event_id)
        event_title = sub.event_title if sub else f"Event #{event_id}"

        try:
            pair = await self._fetch_with_retry(
                lambda eid=event_id: hltv_data.get_event_matches_with_hints(eid),
                event_id=event_id,
            )
            if not pair:
                return upcoming

            matches, hints = pair

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
                    local_next = minutes_until if local_next is None else min(local_next, minutes_until)
                else:
                    elapsed_minutes = abs(seconds_until) / 60
                    if elapsed_minutes <= OVERDUE_THRESHOLD_MINUTES:
                        local_next = 0 if local_next is None else min(local_next, 0)

            poll_state.next_minutes_hint = local_next

            if not matches:
                return upcoming

            for match in matches:
                if data_manager.is_start_notified(match.id):
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
                    )
                )

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 比赛失败: {e}")
            poll_state.has_fetch_error = True

        return upcoming

    async def check_match_results_for_event(self, event_id: str) -> list[tuple[str, str, ResultInfo]]:
        new_results: list[tuple[str, str, ResultInfo]] = []

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state != "ONGOING":
            return new_results

        sub = data_manager.get_any_subscription_by_event(event_id)
        event_title = sub.event_title if sub else f"Event #{event_id}"

        poll_state = self._get_event_poll_state(event_id)

        try:
            results = await self._fetch_with_retry(
                lambda eid=event_id: hltv_data.get_event_results(eid, max_results=5),
                event_id=event_id,
            )
            if not results:
                return new_results

            for r in results:
                if not data_manager.is_result_notified(r.id):
                    new_results.append((event_id, event_title, r))
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 结果失败: {e}")
            poll_state.has_fetch_error = True

        return new_results

    async def send_match_reminder(self, bot: Bot, match: UpcomingMatch) -> None:
        groups = data_manager.get_groups_by_event(match.event_id)
        if not groups:
            return

        try:
            start_time_str = "LIVE" if match.minutes_until <= 0 else match.start_time.strftime("%H:%M")
            img = await render_reminder(
                team1=match.team1,
                team2=match.team2,
                event_title=match.event_title,
                minutes_until=match.minutes_until,
                start_time_str=start_time_str,
                maps=match.maps,
            )
            msg = MessageSegment.image(img)
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 渲染提醒图片失败，使用文本消息: {e}")
            start_time_str = "LIVE" if match.minutes_until <= 0 else match.start_time.strftime("%H:%M")
            bo_text = f"BO{match.maps}" if match.maps else ""
            msg = (
                f"""🔴 比赛已开始

🏆 {match.event_title}

⏰ {start_time_str}
🎮 {match.team1} vs {match.team2}
{f'📋 {bo_text}' if bo_text else ''}""".strip()
            )

        for group_id in groups:
            try:
                await bot.send_group_msg(group_id=group_id, message=msg)
                logger.info(
                    f"[HLTV Scheduler] 已发送比赛提醒到群 {group_id}: {match.team1} vs {match.team2}"
                )
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 发送比赛提醒到群 {group_id} 失败: {e}")

        data_manager.add_notified_start(match.match_id)

    async def send_match_result(
        self, bot: Bot, event_id: str, event_title: str, result: ResultInfo
    ) -> None:
        groups = data_manager.get_groups_by_event(event_id)
        if not groups:
            return

        any_success = False

        try:
            stats = await self._fetch_with_retry(
                lambda: hltv_data.get_match_stats(
                    match_id=result.id,
                    team1=result.team1,
                    team2=result.team2,
                    event_title=event_title,
                ),
                event_id=event_id,
            )

            if stats:
                expected_maps = 0
                try:
                    if str(stats.score1).isdigit() and str(stats.score2).isdigit():
                        expected_maps = int(stats.score1) + int(stats.score2)
                except Exception:
                    expected_maps = 0

                played_maps = [
                    m for m in (stats.maps or []) if m.score_team1 != "-" and m.score_team2 != "-"
                ]

                if expected_maps > 0:
                    if len(played_maps) < expected_maps:
                        logger.info(
                            f"[HLTV Scheduler] match {result.id} stats 未更新完整："
                            f"expected_maps={expected_maps}, played_maps={len(played_maps)}，跳过本次推送等待下次轮询"
                        )
                        return

                    missing_details = [
                        m.map_name
                        for m in played_maps
                        if m.map_name not in (stats.map_stats_details or {})
                    ]
                    if missing_details:
                        logger.info(
                            f"[HLTV Scheduler] match {result.id} 单图数据未更新完整："
                            f"missing_map_details={missing_details}，跳过本次推送等待下次轮询"
                        )
                        return

                img = await render_stats(stats)
                msg = MessageSegment.text("🏁 比赛已结束\n\n") + MessageSegment.image(img)
            else:
                msg = f"""🏁 比赛已结束

🏆 {event_title}

{result.team1} {result.score1} - {result.score2} {result.team2}"""

            for group_id in groups:
                try:
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    any_success = True
                    logger.info(
                        f"[HLTV Scheduler] 已发送比赛结果到群 {group_id}: {result.team1} vs {result.team2}"
                    )
                except Exception as e:
                    logger.error(f"[HLTV Scheduler] 发送比赛结果到群 {group_id} 失败: {e}")

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 处理比赛结果 {result.id} 失败: {e}")

        if any_success:
            data_manager.add_notified_result(result.id)
        else:
            logger.warning(
                f"[HLTV Scheduler] 比赛结果 {result.id} 所有群发送失败，不标记为已推送，下轮将重试"
            )

    async def run_check_for_event(self, event_id: str) -> dict:
        """执行某个赛事的一轮检查"""
        result: dict = {"event_id": event_id, "upcoming_matches": [], "new_results": [], "errors": []}
        poll_state = self._get_event_poll_state(event_id)
        poll_state.has_fetch_error = False

        if event_id not in data_manager.get_all_subscribed_event_ids():
            return result

        state = get_event_state(self._tz, self._end_grace_days, event_id)
        if state not in ("ONGOING", "UPCOMING"):
            self.ensure_event_job_state(event_id)
            return result

        try:
            try:
                bot = get_bot()
            except Exception:
                logger.debug(f"[HLTV Scheduler] 无法获取 Bot，跳过推送 (event={event_id})")
                return result

            upcoming = await self.check_match_starts_for_event(event_id)
            result["upcoming_matches"] = upcoming
            for match in upcoming:
                await self.send_match_reminder(bot, match)

            new_results = await self.check_match_results_for_event(event_id)
            result["new_results"] = [(eid, title, r.id) for eid, title, r in new_results]
            for eid, title, r in new_results:
                await self.send_match_result(bot, eid, title, r)

            self._apply_adaptive_schedule(event_id, poll_state)

            logger.info(
                f"[HLTV Scheduler] 检查完成(event={event_id}): {len(upcoming)} 场即将开始, {len(new_results)} 场新结果"
            )
        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查失败(event={event_id}): {e}")
            result["errors"].append(str(e))

        return result

    async def run_check(self) -> dict:
        """手动执行全量检查（调试命令/兼容旧接口）"""
        result: dict = {"upcoming_matches": [], "new_results": [], "errors": []}
        event_ids = sorted(data_manager.get_all_subscribed_event_ids())

        for event_id in event_ids:
            r = await self.run_check_for_event(event_id)
            result["upcoming_matches"].extend(r.get("upcoming_matches", []))
            result["new_results"].extend(r.get("new_results", []))
            result["errors"].extend(r.get("errors", []))

        return result

    async def _try_refresh_subscription_meta(self, event_id: str) -> bool:
        """尝试补全 UNKNOWN 赛事元信息（start/end/title）"""
        try:
            info = await self._fetch_with_retry(
                lambda eid=event_id: hltv_data.get_event_info(eid),
                event_id=event_id,
            )
            if not info:
                return False

            return data_manager.update_subscription_meta(
                event_id,
                event_title=info.title or None,
                start_date=info.start_date or None,
                end_date=info.end_date or None,
            )
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 补全赛事元信息失败(event={event_id}): {e}")
            return False

    async def daily_maintenance(self) -> dict:
        """每日维护：自动取消已结束订阅 + 清理去重状态"""
        removed_events: list[str] = []
        failed_events: list[str] = []
        checked_events = sorted(data_manager.get_all_subscribed_event_ids())
        auto_unsub_delay_days = max(0, plugin_config.hltv_auto_unsub_delay_days)

        for event_id in checked_events:
            state = get_event_state(self._tz, self._end_grace_days, event_id)

            # UNKNOWN 先尝试补全一次元信息
            if state == "UNKNOWN":
                await self._try_refresh_subscription_meta(event_id)
                state = get_event_state(self._tz, self._end_grace_days, event_id)

            if state == "ENDED":
                sub = data_manager.get_any_subscription_by_event(event_id)
                if not sub:
                    continue
                end_dt = parse_mmdd(self._tz, sub.end_date, end_of_day=True)
                if not end_dt:
                    failed_events.append(event_id)
                    continue

                if datetime.now(self._tz) <= end_dt + timedelta(days=auto_unsub_delay_days):
                    continue

                if data_manager.unsubscribe_event_global(event_id):
                    removed_events.append(event_id)
                    self._remove_event_job(event_id)

        removed_starts, removed_results = data_manager.cleanup_notified_state(
            plugin_config.hltv_notified_ttl_days
        )

        self.ensure_job_state()
        self.refresh_wakeup_jobs()

        logger.info(
            f"[HLTV Scheduler] 每日维护完成: removed_events={removed_events}, "
            f"failed_events={failed_events}, "
            f"cleaned_starts={removed_starts}, cleaned_results={removed_results}"
        )

        return {
            "removed_events": removed_events,
            "failed_events": failed_events,
            "cleaned_starts": removed_starts,
            "cleaned_results": removed_results,
        }

    async def get_upcoming_info(self) -> list[UpcomingMatch]:
        """获取所有即将开始的比赛信息（用于测试命令）"""
        upcoming: list[UpcomingMatch] = []
        now = datetime.now(self._tz)

        event_ids = data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            return upcoming

        for event_id in event_ids:
            if get_event_state(self._tz, self._end_grace_days, event_id) == "ENDED":
                continue

            sub = data_manager.get_any_subscription_by_event(event_id)
            event_title = sub.event_title if sub else f"Event #{event_id}"

            try:
                matches = await hltv_data.get_event_matches(event_id)
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
                            )
                        )
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 获取赛事 {event_id} 比赛失败: {e}")
                continue

        upcoming.sort(key=lambda x: x.start_time)
        return upcoming
