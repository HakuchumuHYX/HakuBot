from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .config_manager import load_config, save_config

# 配置管理命令
list_schedules = on_command("查看定时任务", permission=SUPERUSER, priority=1, block=True)
reload_schedules = on_command("重载定时任务", permission=SUPERUSER, priority=1, block=True)


@list_schedules.handle()
async def list_schedules_handler(matcher: Matcher):
    """查看当前定时任务配置"""
    config_data = load_config()
    schedules = config_data.get("schedules", [])

    if not schedules:
        await matcher.send("当前没有配置定时任务")
        return

    message = "当前定时任务配置:\n\n"
    for i, schedule in enumerate(schedules, 1):
        message += f"{i}. {schedule['id']}\n"
        message += f"   时间: {schedule['hour']}:{schedule['minute']:02d}\n"
        message += f"   消息: {schedule['message']}\n\n"

    await matcher.send(message.strip())


@reload_schedules.handle()
async def reload_schedules_handler(matcher: Matcher):
    """重新加载定时任务配置"""
    try:
        # 直接从 __init__ 模块导入，避免循环导入
        from . import reload_schedule_config
        reload_schedule_config()
        await matcher.send("已重新加载定时任务配置")
    except Exception as e:
        logger.error(f"重新加载定时任务失败: {e}")
        await matcher.send(f"重新加载失败: {e}")