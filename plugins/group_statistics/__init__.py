from nonebot import get_driver, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")

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


# 机器人关闭时保存数据
@get_driver().on_shutdown
async def shutdown_plugin():
    """插件关闭时保存数据"""
    data_manager.save_stats()
    logger.info("群聊消息统计插件数据已保存")
