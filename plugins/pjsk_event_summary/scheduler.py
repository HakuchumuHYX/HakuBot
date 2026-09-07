from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown
import asyncio

from nonebot import get_driver, require

from plugins.pjsk_event_summary.services.index import (
    INITIAL_REBUILD_DELAY_SECONDS,
    REBUILD_INTERVAL_HOURS,
    rebuild_event_index,
)
from utils.logging import get_logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

logger = get_logger("pjsk_event_summary.scheduler")
driver = get_driver()


async def _run_rebuild(reason: str) -> None:
    try:
        rebuild_event_index(reason=reason)
    except Exception as e:
        logger.exception(f"MoeSekai-Hub 事件索引重建任务失败 ({reason}): {e}")


@on_plugin_startup(driver, "pjsk_event_summary")
async def _startup_rebuild() -> None:
    async def delayed_rebuild() -> None:
        await asyncio.sleep(INITIAL_REBUILD_DELAY_SECONDS)
        await _run_rebuild("startup")

    runtime.spawn(delayed_rebuild(), name="pjsk_event_summary")


@scheduler.scheduled_job(
    "interval",
    hours=REBUILD_INTERVAL_HOURS,
    id="rebuild_moesekai_hub_event_index",
)
async def scheduled_rebuild_moesekai_hub_index() -> None:
    await _run_rebuild("scheduled")
