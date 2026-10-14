from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown
from utils.paths import PluginPaths
from nonebot import require, get_driver, logger
from nonebot.adapters.onebot.v11 import Bot
import json
from pathlib import Path

# 导入定时任务依赖
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from core.access import is_plugin_enabled
from utils.logging import get_exc_desc

# 获取所有机器人实例
bots = get_driver().bots

# 配置文件路径
CONFIG_FILE = PluginPaths("daily_message").config / "config.json"


def load_schedule_config():
    """加载定时任务配置；文件缺失或读坏时不设置任何任务，避免往群里发占位消息"""
    if not CONFIG_FILE.exists():
        logger.warning("配置文件不存在，不设置定时任务")
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            schedules = json.load(f).get("schedules", [])
    except Exception:
        logger.exception("加载配置文件失败，不设置定时任务")
        return []
    logger.info(f"从配置文件加载了 {len(schedules)} 个定时任务")
    return schedules


async def get_group_bot_map():
    """获取群组到机器人的映射，每个群只由第一个认领它的机器人负责"""
    group_bot_map = {}

    for bot in bots.values():
        try:
            # 获取机器人加入的所有群组
            groups = await bot.get_group_list()
            for group in groups:
                group_id = str(group["group_id"])
                # 群已被前一个机器人认领则跳过，避免多 bot 重复发送
                if group_id not in group_bot_map:
                    group_bot_map[group_id] = bot
        except Exception as e:
            logger.warning(f"获取机器人 {bot.self_id} 的群列表失败: {get_exc_desc(e)}")

    return group_bot_map


async def send_message_to_enabled_groups(message: str):
    """发送消息到所有启用了插件的群组"""
    group_bot_map = await get_group_bot_map()

    if not group_bot_map:
        logger.error("未找到任何群组")
        return

    enabled_groups = []
    for group_id in group_bot_map:
        user_id = "0"
        if is_plugin_enabled("daily_message", group_id, user_id):
            enabled_groups.append(group_id)

    if not enabled_groups:
        logger.info("没有启用了定时消息插件的群组")
        return

    failed = 0
    for group_id in enabled_groups:
        bot = group_bot_map[group_id]
        try:
            await bot.send_group_msg(group_id=int(group_id), message=message)
            logger.info(f"成功发送消息到群 {group_id}: {message}")
        except Exception as e:
            failed += 1
            logger.warning(f"发送消息到群 {group_id} 失败: {get_exc_desc(e)}")
    if failed == len(enabled_groups):
        logger.error(f"定时消息全部群发送失败（{failed} 个群）: {message}")


def setup_scheduled_jobs():
    """设置定时任务"""
    schedules = load_schedule_config()

    # 清除所有现有的定时任务（避免重复）
    for job in scheduler.get_jobs():
        if job.id.startswith("daily_message_"):
            scheduler.remove_job(job.id)

    for schedule in schedules:
        job_id = f"daily_message_{schedule.get('id')}"
        hour = schedule.get("hour")
        minute = schedule.get("minute")
        message = schedule.get("message")

        if not all([job_id, hour is not None, minute is not None, message]):
            logger.error(f"定时任务配置不完整: {schedule}")
            continue

        # 动态创建定时任务
        @scheduler.scheduled_job("cron", hour=hour, minute=minute, id=job_id)
        async def scheduled_job(msg=message):
            await send_message_to_enabled_groups(msg)

        logger.debug(f"已设置定时任务 {job_id}: {hour}:{minute:02d} -> {message}")


# 重新加载配置的函数
def reload_schedule_config():
    """重新加载定时任务配置"""
    setup_scheduled_jobs()
    logger.info("已重新加载定时任务配置")


# 插件加载时设置定时任务
@on_plugin_startup(get_driver(), "daily_message")
async def init_plugin():
    """插件初始化"""
    setup_scheduled_jobs()


# 模块底部导入命令处理器，注册命令（command_handler 内对本模块使用延迟导入，避免循环导入）
from plugins.daily_message import command_handler
