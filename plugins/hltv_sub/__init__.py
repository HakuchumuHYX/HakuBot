"""
HLTV 订阅插件入口
"""

import asyncio

from nonebot import get_driver, logger

from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown

from plugins.hltv_sub.client import HLTVFetchError, hltv_client
from plugins.hltv_sub.handler import hltv_handler
from plugins.hltv_sub.data_manager import data_manager
from plugins.hltv_sub.scheduler import hltv_scheduler

# 导入即注册命令。
from plugins.hltv_sub import commands as _commands  # noqa: F401


driver = get_driver()


@on_plugin_shutdown(driver, "hltv_sub")
async def cleanup():
    """清理资源"""
    await hltv_client.close()


async def _delayed_init():
    """延迟初始化，等待一段时间后再执行"""
    await asyncio.sleep(10)
    try:
        if data_manager.has_enabled_groups():
            try:
                await hltv_client.start()
            except HLTVFetchError as e:
                logger.info(f"[HLTV] 会话准备暂未完成，后续检查恢复：{e}")
            else:
                await hltv_scheduler.auto_subscribe_big_events()
            await hltv_handler.init_existing_results()
        await hltv_scheduler.daily_maintenance()
    except Exception:
        logger.exception("[HLTV] 启动初始化未完成，后续周期检查继续处理")
    hltv_scheduler.start()


@on_plugin_startup(driver, "hltv_sub")
async def start_scheduler():
    runtime.spawn(_delayed_init(), name="hltv_sub")
