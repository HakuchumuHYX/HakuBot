"""
wakeup date job 管理

说明：
- 对 NOT_ONGOING 创建唤醒任务，在 start_dt - UPCOMING_WINDOW_HOURS 触发。
- 这里仅负责 job 的创建/移除/扫描清理；具体唤醒时的动作由 callback 决定。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Awaitable, Callable

from nonebot.log import logger
from nonebot_plugin_apscheduler import scheduler

from ..data_manager import data_manager
from .constants import UPCOMING_WINDOW_HOURS, WAKEUP_JOB_PREFIX
from .state import get_event_state, parse_mmdd


def wakeup_job_id(event_id: str) -> str:
    return f"{WAKEUP_JOB_PREFIX}{event_id}"


def remove_wakeup_job(event_id: str) -> None:
    job_id = wakeup_job_id(event_id)
    try:
        scheduler.remove_job(job_id)
        logger.info(f"[HLTV Scheduler] 已移除唤醒任务: {job_id}")
    except Exception:
        # job 可能不存在
        pass


def schedule_wakeup_job(
    event_id: str, run_date: datetime, on_wakeup: Callable[[str], Awaitable[None]]
) -> None:
    """创建/更新一次性唤醒任务：在 run_date 时调用 on_wakeup(event_id)"""
    job_id = wakeup_job_id(event_id)
    try:
        scheduler.add_job(
            on_wakeup,
            trigger="date",
            run_date=run_date,
            args=[event_id],
            id=job_id,
            replace_existing=True,
        )
        logger.info(
            f"[HLTV Scheduler] 已创建/更新唤醒任务: {job_id}, run_date={run_date.isoformat()}"
        )
    except Exception as e:
        logger.warning(f"[HLTV Scheduler] 创建/更新唤醒任务失败 ({job_id}): {e}")


def refresh_wakeup_jobs(
    tz,
    end_grace_days: int,
    on_wakeup: Callable[[str], Awaitable[None]],
) -> None:
    """根据当前订阅状态重建/清理唤醒任务（幂等）

    apscheduler 默认内存 jobstore，重启会丢 job，所以：
    - 启动时要跑一遍
    - 订阅/取消订阅、开启/关闭时也要跑一遍
    """
    now = datetime.now(tz)
    event_ids = data_manager.get_all_subscribed_event_ids()

    # 1) 先清理“已不在订阅集合里”的遗留 wakeup job（例如订阅被替换/取消）
    try:
        for job in scheduler.get_jobs():
            if not job.id.startswith(WAKEUP_JOB_PREFIX):
                continue
            event_id = job.id.removeprefix(WAKEUP_JOB_PREFIX)
            if event_id not in event_ids:
                remove_wakeup_job(event_id)
    except Exception as e:
        logger.debug(f"[HLTV Scheduler] 扫描/清理遗留唤醒任务失败: {e}")

    # 2) 对当前订阅集合，按状态创建/更新/移除 wakeup job
    for event_id in event_ids:
        state = get_event_state(tz, end_grace_days, event_id)
        sub = data_manager.get_any_subscription_by_event(event_id)

        # 只有 NOT_ONGOING（窗口外）才需要 wakeup job；其他状态移除即可
        if state != "NOT_ONGOING" or not sub or not sub.start_date:
            remove_wakeup_job(event_id)
            continue

        start_dt = parse_mmdd(tz, sub.start_date, end_of_day=False)
        if not start_dt:
            remove_wakeup_job(event_id)
            continue

        run_date = start_dt - timedelta(hours=UPCOMING_WINDOW_HOURS)

        # 兜底：如果 run_date 已经过了（说明其实应在 UPCOMING 内），就不建 wakeup
        if run_date <= now:
            remove_wakeup_job(event_id)
            continue

        schedule_wakeup_job(event_id, run_date, on_wakeup)
