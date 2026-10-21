from nonebot import on_command, logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from plugins.daily_message import config_manager

# 配置管理命令
list_schedules = on_command(
    "查看定时任务", permission=SUPERUSER, priority=5, block=True
)
reload_schedules = on_command(
    "重载定时任务", permission=SUPERUSER, priority=5, block=True
)


@list_schedules.handle()
async def list_schedules_handler(matcher: Matcher):
    """查看当前定时任务配置"""
    schedules = config_manager.plugin_config.schedules

    if not schedules:
        await matcher.send("当前没有配置定时任务")
        return

    message = "当前定时任务配置:\n\n"
    for i, schedule in enumerate(schedules, 1):
        message += f"{i}. {schedule.id}\n"
        message += f"   时间: {schedule.hour}:{schedule.minute:02d}\n"
        message += f"   消息: {schedule.message}\n\n"

    await matcher.send(message.strip())


@reload_schedules.handle()
async def reload_schedules_handler(matcher: Matcher):
    """重新加载定时任务配置"""
    try:
        # 直接从 __init__ 模块导入，避免循环导入
        from plugins.daily_message.handlers import reload_schedule_config

        reload_schedule_config()
        await matcher.send("已重新加载定时任务配置")
    except Exception as e:
        logger.exception("重新加载定时任务失败")
        await matcher.send(f"重新加载失败: {e}")
