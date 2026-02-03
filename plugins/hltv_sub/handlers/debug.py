"""
超级用户调试命令：hltv_check / hltv_trigger
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.exception import FinishedException
from nonebot.log import logger

from ..data_manager import data_manager
from ..scheduler import hltv_scheduler


hltv_check = on_command("hltv_check", priority=1, block=True)


@hltv_check.handle()
async def handle_hltv_check(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)

    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return

    await hltv_check.send("正在检查即将开始的比赛...")

    try:
        upcoming = await hltv_scheduler.get_upcoming_info()

        if not upcoming:
            await hltv_check.finish("暂无即将开始的比赛")
            return

        msg = "📋 即将开始的比赛：\n\n"

        for match in upcoming[:10]:
            if match.minutes_until >= 60:
                hours = match.minutes_until // 60
                mins = match.minutes_until % 60
                time_str = f"{hours}小时{mins}分钟" if mins > 0 else f"{hours}小时"
            else:
                time_str = f"{match.minutes_until}分钟"

            bo_text = f"BO{match.maps}" if match.maps else ""
            notified = "✓" if data_manager.is_start_notified(match.match_id) else ""

            msg += f"⏰ {time_str}后 {notified}\n"
            msg += f"🎮 {match.team1} vs {match.team2}\n"
            msg += f"🏆 {match.event_title}"
            if bo_text:
                msg += f" | {bo_text}"
            msg += "\n\n"

        if len(upcoming) > 10:
            msg += f"... 还有 {len(upcoming) - 10} 场比赛"

        await hltv_check.finish(msg.strip())

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"检查比赛失败: {e}")
        await hltv_check.finish(f"检查失败: {e}")


hltv_trigger = on_command("hltv_trigger", priority=1, block=True)


@hltv_trigger.handle()
async def handle_hltv_trigger(bot: Bot, event: GroupMessageEvent):
    user_id = str(event.user_id)

    superusers = getattr(bot.config, "superusers", set())
    if user_id not in superusers:
        return

    await hltv_trigger.send("正在手动执行定时任务检查...")

    try:
        result = await hltv_scheduler.run_check()

        msg = "📊 检查结果：\n\n"
        msg += f"即将开始：{len(result['upcoming_matches'])} 场\n"
        msg += f"新结果：{len(result['new_results'])} 场\n"

        if result["errors"]:
            msg += f"错误：{len(result['errors'])} 个\n"
            for err in result["errors"][:3]:
                msg += f"  - {err}\n"

        await hltv_trigger.finish(msg.strip())

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"手动触发检查失败: {e}")
        await hltv_trigger.finish(f"执行失败: {e}")
