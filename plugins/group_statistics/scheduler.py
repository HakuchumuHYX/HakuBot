import asyncio
from datetime import datetime, timedelta, timezone

from nonebot import get_bot
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled

from .data_manager import data_manager
from .utils import get_total_messages, get_top_users, reset_daily_stats
from .render import render_daily_stat_image
from ..utils.tools import get_logger

logger = get_logger("group_statistics.scheduler")


TZ_CN = timezone(timedelta(hours=8))


async def send_daily_report(bot, group_id: int):
    """发送每日统计报告

    注意：发送发生在次日 00:00，但统计日期应为前一天。
    """
    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id), "0"):
        return

    total = get_total_messages(group_id)
    top_users = get_top_users(group_id)

    if total == 0:
        return

    stat_date = datetime.now(TZ_CN).date() - timedelta(days=1)
    img_bytes = await render_daily_stat_image(total, top_users, stat_date=stat_date)

    try:
        await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(img_bytes))
        logger.info(f"已发送群 {group_id} 的每日统计报告")
    except Exception as e:
        logger.exception(f"发送群 {group_id} 的统计报告失败: {e}")


@scheduler.scheduled_job("cron", hour=0, minute=0, second=0)
async def daily_statistics_task():
    """每日0点执行统计任务"""
    logger.info("开始执行每日统计任务...")

    try:
        bot = get_bot()

        # 获取所有有统计数据的群组
        groups_with_stats = list(data_manager.group_stats.keys())

        # 为每个有统计数据的群组发送统计报告（如果启用）
        for group_id in groups_with_stats:
            if get_total_messages(group_id) > 0:
                await send_daily_report(bot, group_id)
                await asyncio.sleep(1)  # 避免发送过快

        # 重置统计数据
        reset_daily_stats()

        logger.info("每日统计任务完成")

    except Exception as e:
        logger.exception(f"每日统计任务执行失败: {e}")
