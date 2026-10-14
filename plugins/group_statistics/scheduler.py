import asyncio
from datetime import datetime, timedelta, timezone

from nonebot import get_bot, logger
from nonebot_plugin_apscheduler import scheduler

# 导入管理模块
from core.access import is_plugin_enabled

from plugins.group_statistics.data_manager import data_manager
from plugins.group_statistics.utils import (
    get_total_messages,
    get_top_users,
    reset_daily_stats,
)
from plugins.group_statistics.render import render_daily_stat_image
from utils.onebot.media import image_segment
from utils.logging import get_exc_desc


TZ_CN = timezone(timedelta(hours=8))


async def send_daily_report(bot, group_id: int) -> bool | None:
    """发送每日统计报告；返回是否发出，未启用或无消息时返回 None

    注意：发送发生在次日 00:00，但统计日期应为前一天。
    """
    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id), "0"):
        return None

    total = get_total_messages(group_id)
    top_users = get_top_users(group_id)

    if total == 0:
        return None

    stat_date = datetime.now(TZ_CN).date() - timedelta(days=1)
    img_bytes = await render_daily_stat_image(total, top_users, stat_date=stat_date)

    try:
        await bot.send_group_msg(group_id=group_id, message=image_segment(img_bytes))
        logger.info(f"已发送群 {group_id} 的每日统计报告")
        return True
    except Exception as e:
        logger.warning(f"发送群 {group_id} 的统计报告失败: {get_exc_desc(e)}")
        return False


@scheduler.scheduled_job("cron", hour=0, minute=0, second=0)
async def daily_statistics_task():
    """每日0点执行统计任务"""
    logger.debug("开始执行每日统计任务...")

    try:
        bot = get_bot()

        # 获取所有有统计数据的群组
        groups_with_stats = list(data_manager.group_stats.keys())

        # 为每个有统计数据的群组发送统计报告（如果启用）
        # 单个群失败不影响其他群，也不影响循环结束后的重置
        sent = attempted = 0
        for group_id in groups_with_stats:
            try:
                if get_total_messages(group_id) > 0:
                    ok = await send_daily_report(bot, group_id)
                    if ok is not None:
                        attempted += 1
                        sent += ok
                    await asyncio.sleep(1)  # 避免发送过快
            except Exception as e:
                attempted += 1
                logger.exception(f"发送群 {group_id} 的每日统计报告失败: {e}")

        # 重置统计数据
        reset_daily_stats()

        if attempted and not sent:
            logger.error(f"每日统计报告全部群发送失败（{attempted} 个群）")
        else:
            logger.info(f"每日统计任务完成：已发送 {sent}/{attempted} 群")

    except Exception as e:
        logger.exception(f"每日统计任务执行失败: {e}")
