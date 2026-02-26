"""
scheduler 启动/注册逻辑

职责：
- 注册 apscheduler interval job（hltv_check）
- 注册 nonebot startup hook（延迟初始化）
- 绑定 core.HLTVScheduler 的 job 控制方法到 apscheduler
"""

from __future__ import annotations

import asyncio

from nonebot import get_driver, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .constants import DEFAULT_INTERVAL_MINUTES, JOB_ID
from .core import HLTVScheduler

hltv_scheduler = HLTVScheduler()

_SCHEDULER_SETUP_DONE = False


def _pause_job() -> None:
    try:
        scheduler.pause_job(JOB_ID)
        logger.info("[HLTV Scheduler] 已暂停定时任务（无进行中赛事）")
    except Exception:
        # job 可能不存在或已暂停
        pass


def _resume_job() -> None:
    try:
        scheduler.resume_job(JOB_ID)
        logger.info("[HLTV Scheduler] 已恢复定时任务（存在进行中赛事）")
    except Exception:
        # job 可能不存在或未暂停
        pass


def _reschedule_job_interval(minutes: int) -> None:
    """保持与原 scheduler.py 的 reschedule 行为一致，并同步更新 _current_interval_minutes"""
    minutes = max(DEFAULT_INTERVAL_MINUTES, int(minutes))
    if minutes == hltv_scheduler._current_interval_minutes:
        return

    try:
        scheduler.reschedule_job(JOB_ID, trigger="interval", minutes=minutes)
        logger.info(
            f"[HLTV Scheduler] 自适应轮询：interval {hltv_scheduler._current_interval_minutes}min -> {minutes}min"
        )
        hltv_scheduler._current_interval_minutes = minutes
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 调整定时任务间隔失败: {e}")


# 将 job 控制方法“注入”到 scheduler 实例（避免 core 直接依赖 apscheduler）
hltv_scheduler._pause_job = _pause_job  # type: ignore[assignment]
hltv_scheduler._resume_job = _resume_job  # type: ignore[assignment]
hltv_scheduler._reschedule_job_interval = _reschedule_job_interval  # type: ignore[assignment]


async def _scheduled_check():
    """定时任务入口"""
    await hltv_scheduler.run_check()


async def _delayed_init():
    """延迟初始化，等待一段时间后再执行"""
    await asyncio.sleep(10)
    try:
        count = await hltv_scheduler.init_existing_results()
        if count > 0:
            logger.info(f"[HLTV Scheduler] 启动初始化完成，标记了 {count} 条历史结果")

        # 按当前订阅状态决定是否需要暂停 job（比如只订阅了未开始/已结束赛事）
        hltv_scheduler.ensure_job_state()

        # 重建/清理 wakeup job（重启后 apscheduler 内存 job 会丢）
        hltv_scheduler.refresh_wakeup_jobs()
    except Exception as e:
        logger.error(f"[HLTV Scheduler] 启动初始化失败: {e}")


def setup_scheduler() -> None:
    """显式初始化 scheduler（幂等）

    目的：避免依赖 import side-effect 来注册 job/启动回调。
    """
    global _SCHEDULER_SETUP_DONE
    if _SCHEDULER_SETUP_DONE:
        return

    # 1) 注册 job（若已存在则不重复注册）
    try:
        existing = scheduler.get_job(JOB_ID)
        if existing is None:
            scheduler.add_job(
                _scheduled_check,
                trigger="interval",
                minutes=DEFAULT_INTERVAL_MINUTES,
                id=JOB_ID,
                replace_existing=True,
            )
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 注册定时任务失败: {e}")

    # 2) 注册 startup hook（延迟初始化）
    driver = get_driver()

    async def _on_startup():
        logger.info(f"[HLTV Scheduler] 定时任务已启动，初始间隔 {DEFAULT_INTERVAL_MINUTES} 分钟")
        asyncio.create_task(_delayed_init())

    driver.on_startup(_on_startup)

    _SCHEDULER_SETUP_DONE = True
