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


class MaintenanceMixin:
    async def init_existing_results(self) -> int:
        """启动时初始化：将现有结果标记为已推送，避免重启后误推送"""
        if self._initialized:
            return 0

        event_ids = self.data_manager.get_all_subscribed_event_ids()
        if not event_ids:
            self._initialized = True
            return 0

        count = 0
        for event_id in event_ids:
            try:
                results = await self._fetch_with_retry(
                    lambda eid=event_id: self.hltv_data.get_event_results(
                        eid, max_results=10
                    ),
                    event_id=event_id,
                )
                if results:
                    for r in results:
                        if not self.data_manager.is_result_notified(r.id):
                            self.data_manager.add_notified_result(r.id, force=True)
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
                lambda eid=event_id: self.hltv_data.get_event_results(
                    eid, max_results=max_results
                ),
                event_id=event_id,
            )
            if not results:
                return 0

            count = 0
            for r in results:
                if not self.data_manager.is_result_notified(r.id):
                    self.data_manager.add_notified_result(r.id, force=True)
                    count += 1
            logger.info(
                f"[HLTV Scheduler] 订阅初始化：已标记 {count} 条现有结果为已推送 (event {event_id})"
            )
            return count
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 订阅初始化失败 (event {event_id}): {e}")
            return 0

    async def _try_refresh_subscription_meta(self, event_id: str) -> bool:
        """尝试补全 UNKNOWN 赛事元信息（start/end/title）"""
        try:
            info = await self._fetch_with_retry(
                lambda eid=event_id: self.hltv_data.get_event_info(eid),
                event_id=event_id,
            )
            if not info:
                return False

            return self.data_manager.update_subscription_meta(
                event_id,
                event_title=info.title or None,
                start_date=info.start_date or None,
                end_date=info.end_date or None,
            )
        except Exception as e:
            logger.warning(
                f"[HLTV Scheduler] 补全赛事元信息失败(event={event_id}): {e}"
            )
            return False

    async def _probe_and_drain_pending_results_before_unsubscribe(
        self,
        event_id: str,
        event_title: str,
        *,
        max_rounds: int = 3,
        stable_empty_rounds: int = 2,
        round_delay_seconds: int = 10,
        max_results: int = 10,
    ) -> bool:
        """退订前多轮探测：尽量推送最后结果，避免 finished 竞态漏推"""
        rounds = max(1, int(max_rounds))
        required_empty_rounds = max(1, min(int(stable_empty_rounds), rounds))
        delay_seconds = max(0, int(round_delay_seconds))

        logger.info(
            f"[HLTV Scheduler] final_probe_start event={event_id}, rounds={rounds}, "
            f"required_empty_rounds={required_empty_rounds}, delay={delay_seconds}s"
        )

        consecutive_empty_rounds = 0
        for idx in range(1, rounds + 1):
            results = await self._fetch_with_retry(
                lambda eid=event_id: self.hltv_data.get_event_results(
                    eid, max_results=max_results
                ),
                event_id=event_id,
            )
            if results is None:
                logger.warning(
                    f"[HLTV Scheduler] final_probe_defer_unsubscribe event={event_id}, "
                    f"reason=fetch_failed, round={idx}/{rounds}"
                )
                return False

            pending = [
                r for r in results if not self.data_manager.is_result_notified(r.id)
            ]
            logger.info(
                f"[HLTV Scheduler] final_probe_round event={event_id}, "
                f"round={idx}/{rounds}, pending={len(pending)}"
            )

            if not pending:
                consecutive_empty_rounds += 1
                if consecutive_empty_rounds >= required_empty_rounds:
                    logger.info(
                        f"[HLTV Scheduler] final_probe_allow_unsubscribe event={event_id}, "
                        f"reason=stable_empty_rounds"
                    )
                    return True
            else:
                consecutive_empty_rounds = 0

                groups = self.data_manager.get_groups_by_event(event_id)
                if not groups:
                    # 没有可推送群时，直接记为已处理，避免卡住退订
                    for r in pending:
                        self.data_manager.add_notified_result(r.id, force=True)
                    logger.info(
                        f"[HLTV Scheduler] final_probe_no_groups event={event_id}, "
                        f"marked_notified={len(pending)}"
                    )
                else:
                    try:
                        bot = get_bot()
                    except Exception:
                        logger.warning(
                            f"[HLTV Scheduler] final_probe_defer_unsubscribe event={event_id}, "
                            f"reason=no_bot, round={idx}/{rounds}"
                        )
                        return False

                    for r in pending:
                        await self.send_match_result(bot, event_id, event_title, r)

                    unsent = [
                        r.id
                        for r in pending
                        if not self.data_manager.is_result_notified(r.id)
                    ]
                    if unsent:
                        logger.warning(
                            f"[HLTV Scheduler] final_probe_pending_after_send event={event_id}, "
                            f"round={idx}/{rounds}, unsent={unsent}"
                        )

            if idx < rounds and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        logger.warning(
            f"[HLTV Scheduler] final_probe_defer_unsubscribe event={event_id}, "
            f"reason=max_rounds_exhausted"
        )
        return False

    async def daily_maintenance(self) -> dict:
        """每日维护：自动取消已结束订阅 + 清理去重状态"""
        removed_events: list[str] = []
        failed_events: list[str] = []
        checked_events = sorted(self.data_manager.get_all_subscribed_event_ids())

        for event_id in checked_events:
            state = get_event_state(self._tz, self._end_grace_days, event_id)

            # UNKNOWN 先尝试补全一次元信息
            if state == "UNKNOWN":
                await self._try_refresh_subscription_meta(event_id)
                state = get_event_state(self._tz, self._end_grace_days, event_id)

            sub = self.data_manager.get_any_subscription_by_event(event_id)
            event_title = sub.event_title if sub else f"Event #{event_id}"

            # 1) finished / ENDED：退订前先做多轮最终结果探测
            if state == "ENDED":
                can_unsubscribe = (
                    await self._probe_and_drain_pending_results_before_unsubscribe(
                        event_id=event_id,
                        event_title=event_title,
                    )
                )
                if can_unsubscribe and self.data_manager.unsubscribe_event_global(
                    event_id
                ):
                    removed_events.append(event_id)
                    self._remove_event_job(event_id)
                elif not can_unsubscribe:
                    failed_events.append(event_id)
                continue

            if state == "NOT_ONGOING":
                self._update_unavailable_streak(
                    event_id,
                    is_unavailable=False,
                    reason="",
                )
                continue

            # 2) matches 页面不可用（基于真实响应元信息）也要先做最终结果探测
            health = await self._fetch_with_retry(
                lambda eid=event_id: self.hltv_data.get_event_matches_health(eid),
                event_id=event_id,
            )
            if not health:
                failed_events.append(event_id)
                continue

            should_auto_unsub = self._update_unavailable_streak(
                event_id,
                is_unavailable=health.is_unavailable,
                reason=health.unavailable_reason,
            )
            if health.is_unavailable:
                if self._is_deterministic_matches_unavailable(
                    health.unavailable_reason
                ):
                    if state in ("ONGOING", "UPCOMING"):
                        failed_events.append(event_id)
                        logger.warning(
                            f"[HLTV Scheduler] 跳过自动退订赛事 {event_id}: "
                            f"state={state}, reason={health.unavailable_reason}"
                        )
                        continue
                    if not should_auto_unsub:
                        failed_events.append(event_id)
                        continue
                    can_unsubscribe = (
                        await self._probe_and_drain_pending_results_before_unsubscribe(
                            event_id=event_id,
                            event_title=event_title,
                        )
                    )
                    if can_unsubscribe and self.data_manager.unsubscribe_event_global(
                        event_id
                    ):
                        removed_events.append(event_id)
                        self._remove_event_job(event_id)
                        logger.warning(
                            f"[HLTV Scheduler] 每日维护自动退订赛事 {event_id}: "
                            f"matches 页面不可用(reason={health.unavailable_reason})"
                        )
                    elif not can_unsubscribe:
                        failed_events.append(event_id)
                else:
                    # 非确定性问题（如 403/临时网络失败）不自动退订
                    failed_events.append(event_id)

        removed_starts, removed_results = self.data_manager.cleanup_notified_state(
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
