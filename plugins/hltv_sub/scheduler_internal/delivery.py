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


class DeliveryMixin:
    async def send_match_reminder(self, bot: Bot, match: UpcomingMatch) -> None:
        groups = self.data_manager.get_groups_by_event(match.event_id)
        if not groups:
            return

        try:
            start_time_str = (
                "LIVE"
                if match.minutes_until <= 0
                else match.start_time.strftime("%H:%M")
            )
            img = await render_reminder(
                team1=match.team1,
                team2=match.team2,
                event_title=match.event_title,
                minutes_until=match.minutes_until,
                start_time_str=start_time_str,
                maps=match.maps,
                is_grand_final=match.is_grand_final,
                is_third_place=match.is_third_place,
            )
            msg = image_segment(img)
        except Exception as e:
            logger.warning(f"[HLTV Scheduler] 渲染提醒图片失败，使用文本消息: {e}")
            start_time_str = (
                "LIVE"
                if match.minutes_until <= 0
                else match.start_time.strftime("%H:%M")
            )
            bo_text = f"BO{match.maps}" if match.maps else ""
            stage_text = (
                "GRAND FINAL"
                if match.is_grand_final
                else "3RD PLACE"
                if match.is_third_place
                else ""
            )
            msg = f"""🔴 比赛已开始

🏆 {match.event_title}
{stage_text}

⏰ {start_time_str}
🎮 {match.team1} vs {match.team2}
{f"📋 {bo_text}" if bo_text else ""}""".strip()

        any_success = False
        for group_id in groups:
            try:
                await bot.send_group_msg(group_id=group_id, message=msg)
                any_success = True
                logger.info(
                    f"[HLTV Scheduler] 已发送比赛提醒到群 {group_id}: {match.team1} vs {match.team2}"
                )
            except Exception as e:
                logger.error(f"[HLTV Scheduler] 发送比赛提醒到群 {group_id} 失败: {e}")

        if any_success:
            self.data_manager.add_notified_start(match.match_id, force=True)
        else:
            logger.warning(
                f"[HLTV Scheduler] 比赛提醒 {match.match_id} 所有群发送失败，不标记为已推送，下轮将重试"
            )

    async def send_match_result(
        self, bot: Bot, event_id: str, event_title: str, result: ResultInfo
    ) -> None:
        groups = self.data_manager.get_groups_by_event(event_id)
        if not groups:
            return

        any_success = False

        try:
            stats = await self._fetch_with_retry(
                lambda: self.hltv_data.get_match_stats(
                    match_id=result.id,
                    team1=result.team1,
                    team2=result.team2,
                    event_title=event_title,
                ),
                event_id=event_id,
            )

            block_reason = get_result_stats_push_block_reason(result, stats)
            if block_reason:
                logger.info(
                    f"[HLTV Scheduler] match {result.id} stats 未准备好："
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
                        f"[HLTV Scheduler] 已发送比赛结果到群 {group_id}: {result.team1} vs {result.team2}"
                    )
                except Exception as e:
                    logger.error(
                        f"[HLTV Scheduler] 发送比赛结果到群 {group_id} 失败: {e}"
                    )

        except Exception as e:
            logger.error(f"[HLTV Scheduler] 处理比赛结果 {result.id} 失败: {e}")

        if any_success:
            self.data_manager.add_notified_result(result.id, force=True)
        else:
            logger.warning(
                f"[HLTV Scheduler] 比赛结果 {result.id} 所有群发送失败，不标记为已推送，下轮将重试"
            )

    async def send_completed_map_result(
        self, bot: Bot, completed_map: CompletedMapResult
    ) -> None:
        groups = self.data_manager.get_groups_by_event(completed_map.event_id)
        if not groups:
            return

        if self.data_manager.is_map_result_notified(completed_map.notification_id):
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
                        f"[HLTV Scheduler] 已发送单图结果到群 {group_id}: "
                        f"{completed_map.team1} vs {completed_map.team2} "
                        f"{completed_map.map_name} "
                        f"({completed_map.score1_after_map}-{completed_map.score2_after_map})"
                    )
                except Exception as e:
                    logger.error(
                        f"[HLTV Scheduler] 发送单图结果到群 {group_id} 失败: {e}"
                    )

        except Exception as e:
            logger.error(
                f"[HLTV Scheduler] 处理单图结果 {completed_map.notification_id} 失败: {e}"
            )

        if any_success:
            self.data_manager.add_notified_map_result(
                completed_map.notification_id, force=True
            )
        else:
            logger.warning(
                f"[HLTV Scheduler] 单图结果 {completed_map.notification_id} "
                f"所有群发送失败，不标记为已推送，下轮将重试"
            )
