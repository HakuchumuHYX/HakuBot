"""
HLTVScheduler 核心类
"""

from __future__ import annotations

import asyncio
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
from .constants import ADAPTIVE_INTERVAL_TABLE, DEFAULT_INTERVAL_MINUTES
from .state import get_event_state, has_active_events
from .types import UpcomingMatch
from .wakeup import refresh_wakeup_jobs as _refresh_wakeup_jobs

T = TypeVar("T")


class HLTVScheduler:
    """HLTV 定时任务调度器（拆分后核心实现）"""

    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._initialized = False

        # 自适应轮询状态
        self._current_interval_minutes: int = DEFAULT_INTERVAL_MINUTES
        self._next_minutes_hint: Optional[int] = None

        # 赛事结束判定缓冲（避免时区/页面延迟导致漏推最后结果）
        self._end_grace_days: int = 1

    async def _fetch_with_retry(
        self,
        coro_func: Callable[[], T],
        max_retries: int = 3,
        delay: float = 2.0,
    ) -> Optional[T]:
        """带重试的异步请求"""
        for attempt in range(max_retries):
            try:
                return await coro_func()
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"[HLTV Scheduler] 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}"
                    )
                    return None
                logger.warning(
                    f"[HLTV Scheduler] 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}，{delay * (attempt + 1)}秒后重试"
                )
                await asyncio.sleep(delay * (attempt + 1))
        return None

    # -------------------- Job 控制（pause/resume/reschedule） --------------------
    # 这些方法由 bootstrap 注入/调用（bootstrap 持有 apscheduler.scheduler）

    def _pause_job(self) -> None:
        """由 bootstrap 绑定实现"""
        raise NotImplementedError

    def _resume_job(self) -> None:
        """由 bootstrap 绑定实现"""
        raise NotImplementedError

    def _reschedule_job_interval(self, minutes: int) -> None:
        """由 bootstrap 绑定实现"""
        raise NotImplementedError

    # -------------------- Wakeup 触发器（date job） --------------------

    async def _on_wakeup(self, event_id: str) -> None:
        """start_dt - UPCOMING_WINDOW_HOURS 触发：恢复 check job，并立即跑一轮"""
        logger.info(f"[HLTV Scheduler] 唤醒触发: event_id={event_id}")
        self.ensure_job_state()

        # 立即跑一轮，让自适应 interval 立刻生效（只多一次请求）
        try:
            await self.run_check()
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 唤醒后立即检查失败: {e}")

    def refresh_wakeup_jobs(self) -> None:
        _refresh_wakeup_jobs(self._tz, self._end_grace_days, self._on_wakeup)

    def ensure_job_state(self) -> None:
        """根据当前订阅状态决定是否暂停/恢复 job（对外可调用）

        active = ONGOING 或 UPCOMING
        - active：resume hltv_check
        - 否则：pause hltv_check（NOT_ONGOING 窗口外 / ENDED / UNKNOWN）
        """
        if has_active_events(self._tz, self._end_grace_days):
            self._resume_job()
            # 确保 interval 回到合理值（避免之前被拉到 180min）
            self._reschedule_job_interval(DEFAULT_INTERVAL_MINUTES)
        else:
            self._pause_job()

    # -------------------- 自适应轮询 --------------------

    def _interval_from_next_minutes(self, next_minutes_until: Optional[int]) -> int:
        """根据下一场比赛剩余分钟数，计算建议轮询间隔（分钟）"""
        if next_minutes_until is None:
            return 180
        if next_minutes_until <= 0:
            return 15
        for upper, interval in ADAPTIVE_INTERVAL_TABLE:
            if next_minutes_until <= upper:
                return interval
        return 180

    def _apply_adaptive_schedule(self) -> None:
        """在一次 run_check 后，根据下一场比赛时间动态调整 interval"""
        if not has_active_events(self._tz, self._end_grace_days):
            return

        minutes = self._interval_from_next_minutes(self._next_minutes_hint)

        logger.info(
            f"[HLTV Scheduler] 自适应轮询评估: next_minutes_until={self._next_minutes_hint}, "
            f"target_interval={minutes}min, current_interval={self._current_interval_minutes}min"
        )

        self._reschedule_job_interval(minutes)

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
                    lambda eid=event_id: hltv_data.get_event_results(eid, max_results=10)
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
                lambda eid=event_id: hltv_data.get_event_results(eid, max_results=max_results)
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

            match_time = datetime(now.year, month, day, hour, minute, tzinfo=self._tz)

            # 如果时间已经过去很久，可能是明年的比赛
            if match_time < now - timedelta(days=30):
                match_time = datetime(
                    now.year + 1, month, day, hour, minute, tzinfo=self._tz
                )

            return match_time
        except Exception:
            return None

    async def check_match_starts(self) -> list[UpcomingMatch]:
        """检查即将开始的比赛，返回需要提醒的比赛列表"""
        upcoming: list[UpcomingMatch] = []
        now = datetime.now(self._tz)

        event_ids = data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            self._next_minutes_hint = None
            return upcoming

        # 用于自适应轮询：找全局最近的下一场比赛
        next_minutes_until: Optional[int] = None

        for event_id in event_ids:
            state = get_event_state(self._tz, self._end_grace_days, event_id)
            if state == "ENDED":
                logger.info(f"[HLTV Scheduler] 跳过赛事 {event_id}: state=ENDED")
                continue
            if state not in ("ONGOING", "UPCOMING"):
                # NOT_ONGOING/UNKNOWN：不轮询（窗口外保持 pause）
                logger.info(f"[HLTV Scheduler] 跳过赛事 {event_id}: state={state}")
                continue

            sub = data_manager.get_any_subscription_by_event(event_id)
            event_title = sub.event_title if sub else f"Event #{event_id}"

            try:
                # 单次 fetch：matches（过滤 TBD，用于提醒） + hints（不过滤 TBD，用于自适应轮询）
                pair = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_matches_with_hints(eid)
                )
                if not pair:
                    continue

                matches, hints = pair

                logger.info(
                    f"[HLTV Scheduler] 赛事 {event_id} matches抓取: filtered={len(matches)}, hints={len(hints)} "
                    f"(hints包含TBD时间)"
                )

                # 1) 自适应轮询：优先使用 hints（即使 TBD 也能拿到 data-unix 时间）
                local_next: Optional[int] = None
                for h in hints:
                    if h.is_live:
                        continue
                    match_time = self._parse_match_time(h.date, h.time)
                    if not match_time:
                        continue
                    minutes_until = int((match_time - now).total_seconds() / 60)
                    if minutes_until > 0:
                        if local_next is None or minutes_until < local_next:
                            local_next = minutes_until
                        if next_minutes_until is None or minutes_until < next_minutes_until:
                            next_minutes_until = minutes_until

                logger.info(
                    f"[HLTV Scheduler] 赛事 {event_id} next_minutes_until(hints)={local_next}"
                )

                # 2) 提醒：仍然只对过滤后的 matches 生效（不会对 TBD 发提醒）
                if not matches:
                    continue

                for match in matches:
                    if match.is_live:
                        continue

                    match_time = self._parse_match_time(match.date, match.time)
                    if not match_time:
                        continue

                    minutes_until = int((match_time - now).total_seconds() / 60)

                    # 提醒窗口（12-17分钟）
                    if 12 <= minutes_until <= 17:
                        if not data_manager.is_start_notified(match.id):
                            upcoming.append(
                                UpcomingMatch(
                                    match_id=match.id,
                                    team1=match.team1,
                                    team2=match.team2,
                                    event_id=event_id,
                                    event_title=event_title,
                                    start_time=match_time,
                                    minutes_until=minutes_until,
                                    maps=match.maps,
                                )
                            )

            except Exception as e:
                logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 比赛失败: {e}")
                continue

        self._next_minutes_hint = next_minutes_until
        logger.info(f"[HLTV Scheduler] 本轮全局 next_minutes_until={self._next_minutes_hint}")
        return upcoming

    async def check_match_results(self) -> list[tuple[str, str, ResultInfo]]:
        """检查已结束的比赛，返回 [(event_id, event_title, result), ...]"""
        new_results: list[tuple[str, str, ResultInfo]] = []

        event_ids = data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            return new_results

        for event_id in event_ids:
            state = get_event_state(self._tz, self._end_grace_days, event_id)
            if state == "ENDED":
                continue
            if state != "ONGOING":
                # NOT_ONGOING/UNKNOWN：不轮询（符合“不是 ongoing 不恢复”）
                continue

            sub = data_manager.get_any_subscription_by_event(event_id)
            event_title = sub.event_title if sub else f"Event #{event_id}"

            try:
                results = await self._fetch_with_retry(
                    lambda eid=event_id: hltv_data.get_event_results(eid, max_results=5)
                )
                if not results:
                    continue

                for r in results:
                    if not data_manager.is_result_notified(r.id):
                        new_results.append((event_id, event_title, r))
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 检查赛事 {event_id} 结果失败: {e}")
                continue

        return new_results

    async def send_match_reminder(self, bot: Bot, match: UpcomingMatch) -> None:
        """发送比赛开始提醒"""
        groups = data_manager.get_groups_by_event(match.event_id)
        if not groups:
            return

        try:
            start_time_str = match.start_time.strftime("%H:%M")
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
            start_time_str = match.start_time.strftime("%H:%M")
            bo_text = f"BO{match.maps}" if match.maps else ""
            msg = (
                f"""🔔 比赛即将开始

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
        """发送比赛结果（不再二次请求 results）"""
        groups = data_manager.get_groups_by_event(event_id)
        if not groups:
            return

        try:
            stats = await self._fetch_with_retry(
                lambda: hltv_data.get_match_stats(
                    match_id=result.id,
                    team1=result.team1,
                    team2=result.team2,
                    event_title=event_title,
                )
            )

            if stats:
                img = await render_stats(stats)
                msg = MessageSegment.text("🏁 比赛已结束\n\n") + MessageSegment.image(img)
            else:
                msg = f"""🏁 比赛已结束

🏆 {event_title}

{result.team1} {result.score1} - {result.score2} {result.team2}"""

            for group_id in groups:
                try:
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    logger.info(
                        f"[HLTV Scheduler] 已发送比赛结果到群 {group_id}: {result.team1} vs {result.team2}"
                    )
                except Exception as e:
                    logger.error(f"[HLTV Scheduler] 发送比赛结果到群 {group_id} 失败: {e}")

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 处理比赛结果 {result.id} 失败: {e}")

        data_manager.add_notified_result(result.id)

    async def run_check(self) -> dict:
        """执行一次检查，返回检查结果"""
        result: dict = {"upcoming_matches": [], "new_results": [], "errors": []}

        try:
            # 核心：如果没有 active 赛事（ONGOING/UPCOMING），直接暂停 job 并退出
            if not has_active_events(self._tz, self._end_grace_days):
                logger.info("[HLTV Scheduler] 本轮无 active 赛事，暂停定时任务并跳过检查")
                self._pause_job()
                return result

            # 获取 bot
            try:
                bot = get_bot()
            except Exception:
                logger.debug("[HLTV Scheduler] 无法获取 Bot，跳过推送")
                return result

            # 即将开始提醒
            upcoming = await self.check_match_starts()
            result["upcoming_matches"] = upcoming
            for match in upcoming:
                await self.send_match_reminder(bot, match)

            # 新结果推送
            new_results = await self.check_match_results()
            result["new_results"] = [(eid, title, r.id) for eid, title, r in new_results]
            for event_id, event_title, r in new_results:
                await self.send_match_result(bot, event_id, event_title, r)

            # 自适应轮询（根据下一场比赛时间调整 interval）
            self._apply_adaptive_schedule()

            logger.info(
                f"[HLTV Scheduler] 检查完成: {len(upcoming)} 场即将开始, {len(new_results)} 场新结果"
            )

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 检查失败: {e}")
            result["errors"].append(str(e))

        return result

    async def get_upcoming_info(self) -> list[UpcomingMatch]:
        """获取所有即将开始的比赛信息（用于测试命令）

        说明：此接口用于“查看未来比赛”，不受 ONGOING 限制，但会跳过 ENDED。
        """
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

                    minutes_until = int((match_time - now).total_seconds() / 60)
                    if minutes_until > 0:
                        upcoming.append(
                            UpcomingMatch(
                                match_id=match.id,
                                team1=match.team1,
                                team2=match.team2,
                                event_id=event_id,
                                event_title=event_title,
                                start_time=match_time,
                                minutes_until=minutes_until,
                                maps=match.maps,
                            )
                        )
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 获取赛事 {event_id} 比赛失败: {e}")
                continue

        upcoming.sort(key=lambda x: x.start_time)
        return upcoming
