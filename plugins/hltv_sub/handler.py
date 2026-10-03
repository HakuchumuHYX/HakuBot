"""HLTV 比赛检查、订阅维护与推送执行。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional

import pytz
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.log import logger

from utils.onebot.media import image_segment
from plugins.hltv_sub.client import HLTVFetchError, hltv_client
from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.data_manager import EventSubscription, data_manager
from plugins.hltv_sub.models import (
    EventCheckResult, EventMatchesPage,
    MapStats, MatchInfo, MatchStats, ResultInfo, UpcomingMatch,
)
from plugins.hltv_sub.render import render_reminder, render_stats

UPCOMING_WINDOW_HOURS = 72
OVERDUE_THRESHOLD_MINUTES = 30
EventState = Literal["ONGOING", "UPCOMING", "NOT_ONGOING", "ENDED", "UNKNOWN"]


@dataclass
class CompletedMapResult:
    """BO3/BO5 中已结束、尚未决出整场胜负的单图推送。"""

    event_id: str
    event_title: str
    match_id: str
    team1: str
    team2: str
    bo_maps: int
    map_index: int
    map_name: str
    notification_id: str
    score1_after_map: str
    score2_after_map: str
    single_map_stats: MatchStats


def get_event_state(
    sub: EventSubscription | None, now: datetime, end_grace_days: int
) -> EventState:
    if sub is None:
        return "UNKNOWN"
    try:
        start = date.fromisoformat(sub.start_date)
        end = date.fromisoformat(sub.end_date)
    except ValueError:
        return "UNKNOWN"
    if end < start:
        return "UNKNOWN"
    tz = now.tzinfo
    start_at = tz.localize(datetime.combine(start, time.min))
    expires_at = tz.localize(datetime.combine(
        end + timedelta(days=end_grace_days + 1), time.min
    ))
    if now >= expires_at:
        return "ENDED"
    if now >= start_at:
        return "ONGOING"
    if now >= start_at - timedelta(hours=UPCOMING_WINDOW_HOURS):
        return "UPCOMING"
    return "NOT_ONGOING"


def _is_final_round_score(score1: int, score2: int) -> bool:
    winner = max(score1, score2)
    loser = min(score1, score2)

    if winner == 13:
        return loser <= 11

    if winner >= 16 and (winner - 16) % 3 == 0:
        return winner - loser >= 2

    return False


def _expected_maps_from_result(result: ResultInfo) -> tuple[int, str, Optional[str]]:
    if not (str(result.score1).isdigit() and str(result.score2).isdigit()):
        return 0, "", None

    score1 = int(result.score1)
    score2 = int(result.score2)

    if max(score1, score2) > 5:
        if not _is_final_round_score(score1, score2):
            return 0, "", f"BO1 回合比分未达到终局: score={score1}-{score2}"
        return 1, "round_score_like_result", None

    return score1 + score2, "map_score_result", None


def _score_pair(score1: str, score2: str) -> Optional[tuple[int, int]]:
    if not (str(score1).isdigit() and str(score2).isdigit()):
        return None
    return int(score1), int(score2)


def has_complete_map_details(stats: MatchStats, map_info: MapStats) -> bool:
    players = (stats.map_stats_details or {}).get(map_info.map_name) or []
    teams = {getattr(player, "team", "") for player in players}
    return bool(players) and {"team1", "team2"}.issubset(teams)


def _score_pairs_match(
    score1: str,
    score2: str,
    expected_score1: str,
    expected_score2: str,
) -> bool:
    score_pair = _score_pair(score1, score2)
    expected_pair = _score_pair(expected_score1, expected_score2)
    return (
        score_pair is not None
        and expected_pair is not None
        and score_pair == expected_pair
    )


def _unfinished_played_maps(played_maps: list[MapStats]) -> list[str]:
    unfinished: list[str] = []
    for map_info in played_maps:
        score_pair = _score_pair(map_info.score_team1, map_info.score_team2)
        if score_pair is None or not _is_final_round_score(*score_pair):
            unfinished.append(
                f"{map_info.map_name}({map_info.score_team1}-{map_info.score_team2})"
            )
    return unfinished


def get_result_stats_push_block_reason(
    result: ResultInfo,
    stats: Optional[MatchStats],
) -> Optional[str]:
    """Return a reason when result stats should wait for a later poll."""
    if stats is None:
        return "stats 未获取到"

    expected_maps, expected_maps_reason, score_block_reason = (
        _expected_maps_from_result(result)
    )
    if score_block_reason:
        return score_block_reason

    if expected_maps <= 0:
        return None

    if not _score_pairs_match(stats.score1, stats.score2, result.score1, result.score2):
        return (
            "stats 总比分未同步到结果页: "
            f"result={result.score1}-{result.score2}, stats={stats.score1}-{stats.score2}"
        )

    played_maps = [
        m for m in (stats.maps or []) if m.score_team1 != "-" and m.score_team2 != "-"
    ]
    if len(played_maps) != expected_maps:
        return (
            "stats 未更新完整: "
            f"expected_maps={expected_maps}, played_maps={len(played_maps)}, "
            f"reason={expected_maps_reason}"
        )

    unfinished_maps = _unfinished_played_maps(played_maps)
    if unfinished_maps:
        return f"单图比分未达到终局: unfinished_maps={unfinished_maps}"

    if expected_maps == 1:
        only_map = played_maps[0]
        if not _score_pairs_match(
            only_map.score_team1,
            only_map.score_team2,
            result.score1,
            result.score2,
        ):
            return (
                "BO1 单图比分未同步到结果页: "
                f"result={result.score1}-{result.score2}, "
                f"map={only_map.score_team1}-{only_map.score_team2}"
            )

    incomplete_details = [
        map_info.map_name
        for map_info in played_maps
        if not has_complete_map_details(stats, map_info)
    ]
    if incomplete_details:
        return f"单图数据未更新完整: incomplete_map_details={incomplete_details}"

    return None


def _single_map_stats(
    *,
    stats: MatchStats,
    map_info: MapStats,
    score1_after_map: int,
    score2_after_map: int,
) -> MatchStats:
    map_players = (stats.map_stats_details or {}).get(map_info.map_name) or []
    return MatchStats(
        match_id=stats.match_id,
        team1=stats.team1,
        team2=stats.team2,
        score1=str(score1_after_map),
        score2=str(score2_after_map),
        status=stats.status,
        maps=[map_info],
        players=[],
        map_stats_details={map_info.map_name: map_players},
        vetos=stats.vetos,
        event=stats.event,
    )


def build_completed_map_results(
    *,
    event_id: str,
    event_title: str,
    match_id: str,
    team1: str,
    team2: str,
    bo_maps: int,
    stats: MatchStats | None,
) -> list[CompletedMapResult]:
    if stats is None or bo_maps not in {3, 5}:
        return []

    maps_needed = bo_maps // 2 + 1
    score1_after_map = 0
    score2_after_map = 0
    candidates: list[CompletedMapResult] = []

    for map_index, map_info in enumerate(stats.maps or [], start=1):
        pair = _score_pair(map_info.score_team1, map_info.score_team2)
        if pair is None:
            continue

        round_score1, round_score2 = pair
        if not _is_final_round_score(round_score1, round_score2):
            continue

        if round_score1 > round_score2:
            score1_after_map += 1
        else:
            score2_after_map += 1

        if not has_complete_map_details(stats, map_info):
            continue

        if max(score1_after_map, score2_after_map) >= maps_needed:
            continue

        stable_map_id = map_info.stats_id or f"{map_index}:{map_info.map_name}"
        candidates.append(
            CompletedMapResult(
                event_id=event_id,
                event_title=event_title,
                match_id=match_id,
                team1=team1,
                team2=team2,
                bo_maps=bo_maps,
                map_index=map_index,
                map_name=map_info.map_name,
                notification_id=f"{match_id}:{stable_map_id}",
                score1_after_map=str(score1_after_map),
                score2_after_map=str(score2_after_map),
                single_map_stats=_single_map_stats(
                    stats=stats,
                    map_info=map_info,
                    score1_after_map=score1_after_map,
                    score2_after_map=score2_after_map,
                ),
            )
        )

    return candidates


class HLTVHandler:
    def __init__(self):
        self._tz = pytz.timezone(plugin_config.hltv_timezone)
        self._end_grace_days = max(0, int(plugin_config.hltv_auto_unsub_delay_days))
        self._initialized_events: set[str] = set()
        self._event_run_locks: dict[str, asyncio.Lock] = {}

    def active_subscriptions(self) -> list[EventSubscription]:
        """与调度器一致：只抓进行中或 UPCOMING_WINDOW_HOURS 内开赛的赛事，远期赛事每抓一次都要排队 15 秒以上。"""
        now = datetime.now(self._tz)
        return [
            sub for sub in data_manager.get_subscribed_events()
            if get_event_state(sub, now, self._end_grace_days) in ("ONGOING", "UPCOMING")
        ]

    def cleanup_unsubscribed(self) -> None:
        subscribed = data_manager.get_all_subscribed_event_ids()
        self._initialized_events.intersection_update(subscribed)
        for event_id, lock in list(self._event_run_locks.items()):
            if event_id not in subscribed and not lock.locked():
                self._event_run_locks.pop(event_id)

    async def run_check_for_event(self, event_id: str) -> EventCheckResult:
        lock = self._event_run_locks.setdefault(event_id, asyncio.Lock())
        if lock.locked():
            return EventCheckResult(
                event_id, has_error=True, errors=[f"赛事 {event_id} 正在检查，跳过重复触发"],
            )
        async with lock:
            return await self._run_check_for_event_unlocked(event_id)

    async def _run_check_for_event_unlocked(self, event_id: str) -> EventCheckResult:
        """执行某个赛事的一轮检查"""
        result = EventCheckResult(event_id)
        sub = data_manager.get_subscription(event_id)
        if sub is None or not data_manager.has_enabled_groups():
            return result

        state = get_event_state(sub, datetime.now(self._tz), self._end_grace_days)
        if state not in ("ONGOING", "UPCOMING"):
            return result
        event_title = sub.event_title

        try:
            try:
                bot = get_bot()
            except Exception:
                logger.debug(
                    f"[HLTV] 无法获取 Bot，跳过推送 (event={event_id})"
                )
                result.has_error = True
                result.errors.append("Bot 尚未连接")
                return result

            if event_id not in self._initialized_events:
                await self.initialize_event_results_as_notified(event_id)
            matches_snapshot = await hltv_client.get_event_matches(event_id)
            upcoming = self.check_match_starts_for_event(
                event_id, matches_snapshot, result
            )
            result.start_candidates = len(upcoming)
            for match in upcoming:
                await self.send_match_reminder(bot, event_id, event_title, match)

            completed_maps = await self.check_completed_map_results_for_event(
                event_id, event_title, matches_snapshot, result
            )
            result.map_candidates = len(completed_maps)
            for completed_map in completed_maps:
                await self.send_completed_map_result(bot, completed_map)

            new_results = await self.check_match_results_for_event(
                event_id, result
            )
            result.result_candidates = len(new_results)
            for r in new_results:
                await self.send_match_result(bot, event_id, event_title, r, check=result)

            logger.debug(
                f"[HLTV] 检查完成(event={event_id}): {len(upcoming)} 场即将开始, "
                f"{len(completed_maps)} 张单图结果, {len(new_results)} 场新结果"
            )
        except HLTVFetchError as e:
            result.retry_at = e.retry_at
            result.has_error = True
            result.errors.append(str(e))
            logger.info(f"[HLTV] 本轮停止(event={event_id}): {e}")
        except Exception as e:
            result.has_error = True
            logger.error(f"[HLTV] 检查失败(event={event_id}): {e}")
            result.errors.append(str(e))

        return result

    async def get_upcoming_info(self) -> list[UpcomingMatch]:
        """获取所有即将开始的比赛信息（用于调试命令）"""
        upcoming: list[UpcomingMatch] = []
        now = datetime.now(self._tz)

        for sub in self.active_subscriptions():
            event_id = sub.event_id
            event_title = sub.event_title

            try:
                page = await hltv_client.get_event_matches(event_id)
                for match in page.matches:
                    if match.is_live:
                        continue

                    match_time = match.start_time
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
                                maps=match.maps,
                                is_grand_final=match.is_grand_final,
                                is_third_place=match.is_third_place,
                            )
                        )
            except HLTVFetchError:
                raise
            except Exception as e:
                logger.error(f"[HLTV] 获取赛事 {event_id} 比赛失败: {e}")
                continue

        upcoming.sort(key=lambda x: x.start_time)
        return upcoming

    def check_match_starts_for_event(
        self, event_id: str,
        snapshot: EventMatchesPage, check: EventCheckResult,
    ) -> list[MatchInfo]:
        upcoming: list[MatchInfo] = []
        now = datetime.now(self._tz)

        try:
            matches, hints, meta = snapshot.matches, snapshot.hints, snapshot.meta

            if meta.is_unavailable:
                return upcoming

            if any(m.is_live for m in matches) or any(h.is_live for h in hints):
                check.has_live_match = True
                check.live_seen_at = datetime.now(self._tz)

            logger.debug(
                f"[HLTV] 赛事 {event_id} matches抓取: filtered={len(matches)}, hints={len(hints)}"
            )

            hint_by_id = {h.match_id: h for h in hints}

            local_next: Optional[int] = None
            for h in hints:
                if h.is_live:
                    continue

                match_time = h.start_time
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

            check.next_minutes_hint = local_next

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
                        match_time = h.start_time
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

                logger.debug(
                    f"[HLTV] 开赛提醒触发: match_id={match.id}, event={event_id}, reason={remind_reason}"
                )
                upcoming.append(match)

        except Exception as e:
            logger.error(f"[HLTV] 检查赛事 {event_id} 比赛失败: {e}")
            check.has_error = True
            check.errors.append(str(e))

        return upcoming

    async def check_match_results_for_event(
        self, event_id: str, check: EventCheckResult
    ) -> list[ResultInfo]:
        new_results: list[ResultInfo] = []

        try:
            results = await hltv_client.get_event_results(event_id, max_results=5)
            if not results:
                return new_results

            for r in results:
                if not data_manager.is_result_notified(r.id):
                    new_results.append(r)
        except HLTVFetchError:
            raise
        except Exception as e:
            logger.error(f"[HLTV] 检查赛事 {event_id} 结果失败: {e}")
            check.has_error = True
            check.errors.append(str(e))

        return new_results

    async def check_completed_map_results_for_event(
        self, event_id: str, event_title: str,
        snapshot: EventMatchesPage, check: EventCheckResult,
    ) -> list[CompletedMapResult]:
        completed_maps: list[CompletedMapResult] = []

        try:
            matches, meta = snapshot.matches, snapshot.meta
            if meta.is_unavailable:
                return completed_maps

            for match in matches:
                if not match.is_live:
                    continue
                if match.maps not in {"3", "5"}:
                    continue

                bo_maps = int(match.maps)
                stats = await hltv_client.get_match_stats(
                    match_id=match.id,
                    team1=match.team1,
                    team2=match.team2,
                    event_title=event_title,
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
                    if not data_manager.is_map_result_notified(
                        candidate.notification_id
                    ):
                        completed_maps.append(candidate)

        except HLTVFetchError:
            raise
        except Exception as e:
            logger.error(f"[HLTV] 检查赛事 {event_id} 单图结果失败: {e}")
            check.has_error = True
            check.errors.append(str(e))

        return completed_maps

    async def init_existing_results(self) -> int:
        """启动时初始化：将现有结果标记为已推送，避免重启后误推送"""
        if not data_manager.has_enabled_groups():
            return 0
        # 未激活的赛事会在首次检查时初始化，ENDED 的由每日维护初始化，启动时不必抓。
        event_ids = [sub.event_id for sub in self.active_subscriptions()]
        count = 0
        for event_id in event_ids:
            try:
                lock = self._event_run_locks.setdefault(event_id, asyncio.Lock())
                async with lock:
                    if event_id not in self._initialized_events:
                        count += await self.initialize_event_results_as_notified(event_id)
            except Exception as e:
                logger.error(f"[HLTV] 初始化赛事 {event_id} 结果失败: {e}")
                continue

        logger.info(f"[HLTV] 已初始化 {count} 条历史结果记录")
        return count

    async def initialize_event_results_as_notified(
        self, event_id: str, max_results: int = 10
    ) -> int:
        """订阅进行中赛事时调用：把当前已有结果先标记为已推送，避免订阅后立刻推历史结果"""
        results = await hltv_client.get_event_results(event_id, max_results=max_results)
        count = 0
        for r in results:
            if not data_manager.is_result_notified(r.id):
                data_manager.add_notified_result(r.id)
                count += 1
        self._initialized_events.add(event_id)
        logger.debug(
            f"[HLTV] 订阅初始化：已标记 {count} 条现有结果为已推送 (event {event_id})"
        )
        return count

    async def _try_refresh_subscription_meta(self, event_id: str) -> bool:
        """尝试补全 UNKNOWN 赛事元信息（start/end/title）"""
        try:
            info = await hltv_client.get_event_info(event_id)
            if not info or not info.title or not info.start_date or not info.end_date:
                return False

            data_manager.update_subscription_meta(
                event_id,
                event_title=info.title,
                start_date=info.start_date,
                end_date=info.end_date,
            )
            return True
        except Exception as e:
            logger.warning(
                f"[HLTV] 补全赛事元信息失败(event={event_id}): {e}"
            )
            return False

    async def daily_maintenance(self) -> None:
        cleaned = data_manager.cleanup_notified_state(plugin_config.hltv_notified_ttl_days)
        if cleaned:
            logger.info(f"[HLTV] 已清理 {cleaned} 条过期去重记录")
        if not data_manager.has_enabled_groups():
            return

        for event_id in sorted(data_manager.get_all_subscribed_event_ids()):
            lock = self._event_run_locks.setdefault(event_id, asyncio.Lock())
            if lock.locked():
                continue
            async with lock:
                try:
                    sub = data_manager.get_subscription(event_id)
                    state = get_event_state(sub, datetime.now(self._tz), self._end_grace_days)
                    if state == "UNKNOWN":
                        if not await self._try_refresh_subscription_meta(event_id):
                            continue
                        sub = data_manager.get_subscription(event_id)
                        state = get_event_state(sub, datetime.now(self._tz), self._end_grace_days)
                    if state != "ENDED":
                        if state == "UNKNOWN":
                            logger.warning(f"[HLTV] 赛事 {event_id} 日期仍无效，保留订阅")
                        continue

                    bot = get_bot()
                    results = await hltv_client.get_event_results(
                        event_id, max_results=10, force_refresh=True,
                    )
                    # 未识别到结果不能证明已发完；保留订阅待下次维护。
                    if not results:
                        logger.warning(f"[HLTV] 延后退订 {event_id}：没有可确认的赛果")
                        continue
                    # 首次成功读取仍按启动策略视为历史，复用本次响应。
                    if event_id not in self._initialized_events:
                        for result in results:
                            if not data_manager.is_result_notified(result.id):
                                data_manager.add_notified_result(result.id)
                        self._initialized_events.add(event_id)
                    for result in results:
                        if not data_manager.is_result_notified(result.id):
                            await self.send_match_result(bot, event_id, sub.event_title, result)
                    if all(data_manager.is_result_notified(r.id) for r in results):
                        if data_manager.unsubscribe_event(event_id):
                            logger.info(f"[HLTV] 已自动退订 {event_id} {sub.event_title}")
                except HLTVFetchError as e:
                    logger.info(f"[HLTV] 延后维护赛事 {event_id}：{e}")
                    break
                except Exception as e:
                    logger.error(f"[HLTV] 维护赛事 {event_id} 失败，保留订阅：{e}")

    async def auto_subscribe_big_events(self) -> list[str]:
        """抓取 /events，订阅尚未订阅的 big events。"""
        if not data_manager.has_enabled_groups():
            return []
        try:
            events = await hltv_client.get_big_events()
        except HLTVFetchError as e:
            logger.info(f"[HLTV] 自动订阅跳过: {e}")
            return []
        except Exception as e:
            logger.error(f"[HLTV] 自动订阅失败: {e}")
            return []

        subscribed = data_manager.get_all_subscribed_event_ids()
        added: list[str] = []
        for event in events:
            if (
                not event.id
                or not event.title
                or not event.start_date
                or not event.end_date
            ):
                continue
            if event.id in subscribed:
                continue

            created = data_manager.subscribe_event(
                EventSubscription(
                    event_id=event.id,
                    event_title=event.title,
                    start_date=event.start_date,
                    end_date=event.end_date,
                )
            )
            if not created:
                continue

            subscribed.add(event.id)
            added.append(f"#{event.id} {event.title}")

        logger.info(
            "[HLTV] 自动订阅完成: "
            + (", ".join(added) if added else "无新赛事")
        )
        return added

    async def send_match_reminder(
        self, bot: Bot, event_id: str, event_title: str, match: MatchInfo
    ) -> None:
        groups = data_manager.get_groups_by_event(event_id)
        if not groups:
            return

        try:
            img = await render_reminder(
                team1=match.team1,
                team2=match.team2,
                event_title=event_title,
                maps=match.maps,
                is_grand_final=match.is_grand_final,
                is_third_place=match.is_third_place,
            )
            msg = image_segment(img)
        except Exception as e:
            logger.warning(f"[HLTV] 渲染提醒图片失败，使用文本消息: {e}")
            bo_text = f"BO{match.maps}" if match.maps else ""
            stage_text = (
                "GRAND FINAL"
                if match.is_grand_final
                else "3RD PLACE"
                if match.is_third_place
                else ""
            )
            msg = f"""🔴 比赛已开始

🏆 {event_title}
{stage_text}

⏰ LIVE
🎮 {match.team1} vs {match.team2}
{f"📋 {bo_text}" if bo_text else ""}""".strip()

        any_success = False
        for group_id in groups:
            try:
                await bot.send_group_msg(group_id=group_id, message=msg)
                any_success = True
                logger.info(
                    f"[HLTV] 已发送比赛提醒到群 {group_id}: {match.team1} vs {match.team2}"
                )
            except Exception as e:
                logger.error(f"[HLTV] 发送比赛提醒到群 {group_id} 失败: {e}")

        if any_success:
            data_manager.add_notified_start(match.id)
        else:
            logger.warning(
                f"[HLTV] 比赛提醒 {match.id} 所有群发送失败，不标记为已推送，下轮将重试"
            )

    async def send_match_result(
        self, bot: Bot, event_id: str, event_title: str, result: ResultInfo,
        *, check: EventCheckResult | None = None,
    ) -> None:
        groups = data_manager.get_groups_by_event(event_id)
        if not groups:
            return

        any_success = False

        try:
            try:
                stats = await hltv_client.get_match_stats(
                    match_id=result.id,
                    team1=result.team1,
                    team2=result.team2,
                    event_title=event_title,
                )
            except Exception:
                if check is not None:
                    check.has_error = True
                raise

            block_reason = get_result_stats_push_block_reason(result, stats)
            if block_reason:
                logger.info(
                    f"[HLTV] match {result.id} stats 未准备好："
                    f"{block_reason}，跳过本次推送等待下次轮询"
                )
                return

            img = await render_stats(stats)
            score_line = (
                f"{result.team1} {result.score1}:{result.score2} {result.team2}"
            )
            msg = MessageSegment.text(
                f"🏁 比赛已结束\n{score_line}\n\n"
            ) + image_segment(img)

            for group_id in groups:
                try:
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    any_success = True
                    logger.info(
                        f"[HLTV] 已发送比赛结果到群 {group_id}: {result.team1} vs {result.team2}"
                    )
                except Exception as e:
                    logger.error(
                        f"[HLTV] 发送比赛结果到群 {group_id} 失败: {e}"
                    )

        except HLTVFetchError:
            raise
        except Exception as e:
            if check is not None:
                check.has_error = True
                check.errors.append(str(e))
            logger.error(f"[HLTV] 处理比赛结果 {result.id} 失败: {e}")

        if any_success:
            data_manager.add_notified_result(result.id)
        else:
            logger.warning(
                f"[HLTV] 比赛结果 {result.id} 所有群发送失败，不标记为已推送，下轮将重试"
            )

    async def send_completed_map_result(
        self, bot: Bot, completed_map: CompletedMapResult
    ) -> None:
        groups = data_manager.get_groups_by_event(completed_map.event_id)
        if not groups:
            return

        if data_manager.is_map_result_notified(completed_map.notification_id):
            return

        any_success = False

        try:
            img = await render_stats(completed_map.single_map_stats)
            score_line = (
                f"{completed_map.team1} "
                f"{completed_map.score1_after_map}:{completed_map.score2_after_map} "
                f"{completed_map.team2}"
            )
            msg = MessageSegment.text(
                f"🗺️ 地图已结束 · BO{completed_map.bo_maps} 图{completed_map.map_index}\n"
                f"{score_line}\n\n"
            ) + image_segment(img)

            for group_id in groups:
                try:
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    any_success = True
                    logger.info(
                        f"[HLTV] 已发送单图结果到群 {group_id}: "
                        f"{completed_map.team1} vs {completed_map.team2} "
                        f"{completed_map.map_name} "
                        f"({completed_map.score1_after_map}-{completed_map.score2_after_map})"
                    )
                except Exception as e:
                    logger.error(
                        f"[HLTV] 发送单图结果到群 {group_id} 失败: {e}"
                    )

        except Exception as e:
            logger.error(
                f"[HLTV] 处理单图结果 {completed_map.notification_id} 失败: {e}"
            )

        if any_success:
            data_manager.add_notified_map_result(
                completed_map.notification_id
            )
        else:
            logger.warning(
                f"[HLTV] 单图结果 {completed_map.notification_id} "
                f"所有群发送失败，不标记为已推送，下轮将重试"
            )


hltv_handler = HLTVHandler()
