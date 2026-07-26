import asyncio

from nonebot import get_driver, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled

from .data_manager import data_manager
from .handlers import message_handler, stat_command, sent_handler
from .scheduler import daily_statistics_task


# 在插件加载时
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    logger.info("群聊消息统计插件已加载")


# 定期落盘（record_user_message 只打脏标记，不再每条消息同步写盘）
@scheduler.scheduled_job("interval", seconds=60, id="group_statistics_flush")
async def flush_stats():
    """每 60 秒检查脏标记，有改动时在线程中写盘，避免阻塞事件循环"""
    await asyncio.to_thread(data_manager.flush)


# 机器人关闭时保存数据
@get_driver().on_shutdown
async def shutdown_plugin():
    """插件关闭时保存数据"""
    data_manager.save_stats()
    logger.info("群聊消息统计插件数据已保存")
