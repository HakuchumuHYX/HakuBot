from nonebot import require, get_driver
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot
import json
from pathlib import Path

# 导入定时任务依赖
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from ..plugin_manager import is_plugin_enabled

# 获取所有机器人实例
bots = get_driver().bots

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_schedule_config():
    """加载定时任务配置"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                schedules = config_data.get("schedules", [])
                logger.info(f"从配置文件加载了 {len(schedules)} 个定时任务")
                return schedules
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {
                "schedules": [
                    {
                        "id": "test_message",
                        "hour": 0,
                        "minute": 0,
                        "message": "现在是北京时间，凌晨0点。"
                    }
                ]
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            logger.warning("配置文件不存在，已创建默认配置文件")
            return default_config["schedules"]
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}，使用默认配置")
        # 返回默认配置
        return [
            {
                "id": "test_message",
                "hour": 0,
                "minute": 0,
                "message": "现在是北京时间，凌晨0点。"
            }
        ]


async def get_all_groups():
    """获取所有机器人加入的群组"""
    all_groups = set()

    for bot in bots.values():
        try:
            # 获取机器人加入的所有群组
            groups = await bot.get_group_list()
            for group in groups:
                all_groups.add(str(group["group_id"]))
        except Exception as e:
            logger.error(f"获取机器人 {bot.self_id} 的群列表失败: {e}")

    return all_groups


async def send_message_to_enabled_groups(message: str):
    """发送消息到所有启用了插件的群组"""
    all_groups = await get_all_groups()

    if not all_groups:
        logger.error("未找到任何群组")
        return

    enabled_groups = []
    for group_id in all_groups:
        if is_plugin_enabled("daily_message", group_id):
            enabled_groups.append(group_id)

    if not enabled_groups:
        logger.info("没有启用了定时消息插件的群组")
        return

    for bot in bots.values():
        for group_id in enabled_groups:
            try:
                await bot.send_group_msg(group_id=int(group_id), message=message)
                logger.info(f"成功发送消息到群 {group_id}: {message}")
            except Exception as e:
                logger.error(f"发送消息到群 {group_id} 失败: {e}")


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

        logger.info(f"已设置定时任务 {job_id}: {hour}:{minute:02d} -> {message}")


# 重新加载配置的函数
def reload_schedule_config():
    """重新加载定时任务配置"""
    setup_scheduled_jobs()
    logger.info("已重新加载定时任务配置")


# 插件加载时设置定时任务
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    setup_scheduled_jobs()
    logger.success("定时消息插件 daily_message 加载成功")

    # 延迟导入，避免循环导入
    @get_driver().on_startup
    async def register_commands():
        """注册命令处理器"""
        try:
            from . import command_handler
            logger.debug("定时消息插件命令处理器注册成功")
        except ImportError as e:
            logger.warning(f"注册命令处理器失败: {e}")
