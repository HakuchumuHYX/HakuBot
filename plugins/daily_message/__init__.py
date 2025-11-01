from nonebot import require, get_driver
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import Bot

# 导入定时任务依赖
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from ..plugin_manager import is_plugin_enabled

# 获取所有机器人实例
bots = get_driver().bots


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


@scheduler.scheduled_job("cron", hour=15, minute=0, id="morning_message")
async def schedule_01():
    await send_message_to_enabled_groups("已经下午三点了！不要忘记清上午的烤森哦！")


@scheduler.scheduled_job("cron", hour=22, minute=0, id="evening_message")
async def schedule_02():
    await send_message_to_enabled_groups("已经晚上十点了！不要忘记登陆游戏打每天的挑战live哦！")


# 插件加载时打印日志
logger.success("定时消息插件 daily_message 加载成功")