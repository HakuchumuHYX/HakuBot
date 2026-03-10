"""
scheduler 启动/注册逻辑

职责：
- 注册 per-event apscheduler interval job（hltv_check_{event_id}）
- 注册 daily maintenance job（自动退订 + 去重状态清理）
- 注册 nonebot startup hook（延迟初始化）
- 绑定 core.HLTVScheduler 的 job 控制方法到 apscheduler
"""

from __future__ import annotations

import asyncio
import random

from nonebot import get_driver, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from ..config import plugin_config
from ..data_manager import data_manager
from .constants import DAILY_MAINTENANCE_JOB_ID, DEFAULT_INTERVAL_MINUTES, event_job_id
from .core import HLTVScheduler

hltv_scheduler = HLTVScheduler()

_SCHEDULER_SETUP_DONE = False


def _ensure_event_job(event_id: str) -> None:
    job_id = event_job_id(event_id)

    try:
        existing = scheduler.get_job(job_id)
        if existing is not None:
            return

        async def _scheduled_check(eid: str):
            jitter = max(0, int(plugin_config.hltv_scheduler_jitter_seconds))
            if jitter > 0:
                await asyncio.sleep(random.randint(0, jitter))
            await hltv_scheduler.run_check_for_event(eid)

        scheduler.add_job(
            _scheduled_check,
            trigger="interval",
            minutes=DEFAULT_INTERVAL_MINUTES,
            args=[event_id],
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"[HLTV Scheduler] 已创建赛事定时任务: {job_id}")
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 创建赛事定时任务失败 ({job_id}): {e}")


def _pause_event_job(event_id: str) -> None:
    job_id = event_job_id(event_id)
    try:
        scheduler.pause_job(job_id)
        logger.info(f"[HLTV Scheduler] 已暂停赛事定时任务: {job_id}")
    except Exception:
        pass


def _resume_event_job(event_id: str) -> None:
    job_id = event_job_id(event_id)
    try:
        scheduler.resume_job(job_id)
        logger.info(f"[HLTV Scheduler] 已恢复赛事定时任务: {job_id}")
    except Exception:
        pass


def _remove_event_job(event_id: str) -> None:
    job_id = event_job_id(event_id)
    try:
        scheduler.remove_job(job_id)
        logger.info(f"[HLTV Scheduler] 已移除赛事定时任务: {job_id}")
    except Exception:
        pass


def _reschedule_event_job_interval(event_id: str, minutes: int) -> None:
    minutes = max(DEFAULT_INTERVAL_MINUTES, int(minutes))
    state = hltv_scheduler._get_event_poll_state(event_id)

    if minutes == state.current_interval_minutes:
        return

    job_id = event_job_id(event_id)
    try:
        _ensure_event_job(event_id)
        scheduler.reschedule_job(job_id, trigger="interval", minutes=minutes)
        logger.info(
            f"[HLTV Scheduler] 自适应轮询(event={event_id})："
            f"{state.current_interval_minutes}min -> {minutes}min"
        )
        state.current_interval_minutes = minutes
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 调整赛事定时任务间隔失败 ({job_id}): {e}")


# 将 job 控制方法注入 scheduler 实例（避免 core 直接依赖 apscheduler）
hltv_scheduler._ensure_event_job = _ensure_event_job  # type: ignore[assignment]
hltv_scheduler._pause_event_job = _pause_event_job  # type: ignore[assignment]
hltv_scheduler._resume_event_job = _resume_event_job  # type: ignore[assignment]
hltv_scheduler._remove_event_job = _remove_event_job  # type: ignore[assignment]
hltv_scheduler._reschedule_event_job_interval = _reschedule_event_job_interval  # type: ignore[assignment]


async def _daily_maintenance():
    await hltv_scheduler.daily_maintenance()


async def _delayed_init():
    """延迟初始化，等待一段时间后再执行"""
    await asyncio.sleep(10)
    try:
        count = await hltv_scheduler.init_existing_results()
        if count > 0:
            logger.info(f"[HLTV Scheduler] 启动初始化完成，标记了 {count} 条历史结果")

        # 按当前订阅状态决定每个 event job 的运行状态
        hltv_scheduler.ensure_job_state()

        # 重建/清理 wakeup job（重启后 apscheduler 内存 job 会丢）
        hltv_scheduler.refresh_wakeup_jobs()

        # 启动时先跑一轮每日维护（自动退订/清理）
        await hltv_scheduler.daily_maintenance()
    except Exception as e:
        logger.error(f"[HLTV Scheduler] 启动初始化失败: {e}")


def _ensure_daily_maintenance_job() -> None:
    try:
        existing = scheduler.get_job(DAILY_MAINTENANCE_JOB_ID)
        if existing is not None:
            return

        scheduler.add_job(
            _daily_maintenance,
            trigger="cron",
            hour=4,
            minute=30,
            id=DAILY_MAINTENANCE_JOB_ID,
            replace_existing=True,
        )
        logger.info("[HLTV Scheduler] 已注册每日维护任务 (04:30)")
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 注册每日维护任务失败: {e}")


def setup_scheduler() -> None:
    """显式初始化 scheduler（幂等）"""
    global _SCHEDULER_SETUP_DONE
    if _SCHEDULER_SETUP_DONE:
        return

    # 1) 为当前已订阅赛事预创建 per-event jobs
    for event_id in data_manager.get_all_subscribed_event_ids():
        _ensure_event_job(event_id)

    # 2) 注册 daily maintenance job
    _ensure_daily_maintenance_job()

    # 3) 注册 startup hook（延迟初始化）
    driver = get_driver()

    async def _on_startup():
        logger.info("[HLTV Scheduler] 多赛事定时任务已启动")
        asyncio.create_task(_delayed_init())

    driver.on_startup(_on_startup)

    _SCHEDULER_SETUP_DONE = True
