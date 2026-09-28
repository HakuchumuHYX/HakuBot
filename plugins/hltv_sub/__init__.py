"""
HLTV 订阅插件入口
"""

import asyncio

from nonebot import get_driver
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown

from plugins.hltv_sub.config import Config
from plugins.hltv_sub.client import HLTVFetchError, hltv_client
from plugins.hltv_sub.handler import hltv_handler
from plugins.hltv_sub.data_manager import data_manager
from plugins.hltv_sub.scheduler import hltv_scheduler

# 导入即注册命令。
from plugins.hltv_sub import commands as _commands


__plugin_meta__ = PluginMetadata(
    name="HLTV订阅",
    description="HLTV CS2 赛事订阅和比赛信息查询",
    usage="""命令列表：
- event列表：查看近期大型赛事
- event订阅 [ID]：全局订阅赛事（有接收群时，启动及每 7 天自动订阅近期赛事）
- event取消订阅 [ID]：全局取消订阅，后续自动订阅可能重新加入
- 我的订阅：查看已订阅赛事

- matches列表：查看已订阅赛事的比赛
- results列表：查看已订阅赛事的结果
- stats：按订阅顺序查看首个有数据赛事的最新比赛
- stats [ID]：查看指定比赛数据

- hltv开启：开启本群功能
- hltv关闭：关闭本群功能

- hltv帮助：查看帮助
""",
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

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
