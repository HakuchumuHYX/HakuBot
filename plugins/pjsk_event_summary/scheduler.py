import asyncio

from nonebot import get_driver, require

from ..utils.moesekai_hub import (
    INITIAL_REBUILD_DELAY_SECONDS,
    REBUILD_INTERVAL_HOURS,
    rebuild_event_index,
)
from ..utils.tools import get_logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

logger = get_logger("pjsk_event_summary.scheduler")
driver = get_driver()


async def _run_rebuild(reason: str) -> None:
    try:
        rebuild_event_index(reason=reason)
    except Exception as e:
        logger.exception(f"MoeSekai-Hub 事件索引重建任务失败 ({reason}): {e}")


@driver.on_startup
async def _startup_rebuild() -> None:
    async def delayed_rebuild() -> None:
        await asyncio.sleep(INITIAL_REBUILD_DELAY_SECONDS)
        await _run_rebuild("startup")

    asyncio.create_task(delayed_rebuild())


@scheduler.scheduled_job(
    "interval",
    hours=REBUILD_INTERVAL_HOURS,
    id="rebuild_moesekai_hub_event_index",
)
async def scheduled_rebuild_moesekai_hub_index() -> None:
    await _run_rebuild("scheduled")
