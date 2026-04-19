import asyncio

from nonebot import get_driver, require

from ..utils.moesekai_hub import (
    INITIAL_SYNC_DELAY_SECONDS,
    SYNC_INTERVAL_HOURS,
    sync_repo_and_rebuild_index,
)
from ..utils.tools import get_logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

logger = get_logger("pjsk_event_summary.scheduler")
driver = get_driver()


async def _run_sync(reason: str) -> None:
    try:
        await sync_repo_and_rebuild_index(reason=reason)
    except Exception as e:
        logger.exception(f"MoeSekai-Hub 同步任务失败 ({reason}): {e}")


@driver.on_startup
async def _startup_sync() -> None:
    async def delayed_sync() -> None:
        await asyncio.sleep(INITIAL_SYNC_DELAY_SECONDS)
        await _run_sync("startup")

    asyncio.create_task(delayed_sync())


@scheduler.scheduled_job(
    "interval",
    hours=SYNC_INTERVAL_HOURS,
    id="sync_moesekai_hub_repo",
)
async def scheduled_sync_moesekai_hub() -> None:
    await _run_sync("scheduled")
