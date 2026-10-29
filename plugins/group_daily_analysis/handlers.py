"""日报命令、渲染调试命令、定时推送"""

import asyncio
import time
from datetime import datetime

from nonebot import get_bot, logger, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.permission import SUPERUSER

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from core.access import is_enabled, switch_rule
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment
from plugins.group_daily_analysis.analysis import analyze
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import (
    AnalysisResult,
    GoldenQuote,
    SummaryTopic,
    UserTitle,
)
from plugins.group_daily_analysis.recorder import fetch_messages
from plugins.group_daily_analysis.render import render_report
from plugins.group_daily_analysis.transcript import (
    MESSAGE_TYPES,
    load_faces,
    read_messages,
)


async def _recent_messages(group_id: int):
    end = int(time.time())
    start = end - plugin_config.analysis_days * 86400
    return await asyncio.to_thread(fetch_messages, group_id, start, end)


async def _group_name(bot: Bot, group_id: int) -> str | None:
    try:
        return (await bot.get_group_info(group_id=group_id))["group_name"]
    except Exception as e:
        logger.warning(f"群 {group_id} 群名获取失败，封面不显示群名: {get_exc_desc(e)}")
        return None


async def build_report(bot: Bot, group_id: int) -> bytes | None:
    """消息不够返回 None"""
    rows = await _recent_messages(group_id)
    # 戳一戳、撤回、贴表情也存在库里，不算消息
    count = sum(row["message_type"] in MESSAGE_TYPES for row in rows)
    if count < plugin_config.min_messages_threshold:
        logger.info(
            f"群 {group_id} 消息数量不足 ({count} < {plugin_config.min_messages_threshold})，跳过分析"
        )
        return None

    await load_faces(bot)
    result = await analyze(group_id, rows)
    image = await render_report(result, await _group_name(bot, group_id))
    logger.info(f"群 {group_id} 每日总结生成成功，图片大小: {len(image)} bytes")
    return image


analysis_cmd = on_command(
    "daily_analysis",
    aliases={"今日总结", "群日报"},
    rule=switch_rule("group_daily_analysis"),
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@analysis_cmd.handle()
async def handle_analysis(bot: Bot, event: GroupMessageEvent):
    await analysis_cmd.send("正在生成每日总结，请稍候... (可能需要几十秒)")
    try:
        image = await build_report(bot, event.group_id)
    except Exception as e:
        logger.exception(f"群 {event.group_id} 生成总结失败: {get_exc_desc(e)}")
        await analysis_cmd.finish("生成总结失败，请稍后重试。")
    if image is None:
        await analysis_cmd.finish(
            f"消息数量不足 {plugin_config.min_messages_threshold} 条，无法生成总结。"
        )
    await analysis_cmd.finish(image_segment(image))


# --- 渲染调试：真实统计 + 写死的内容，不调 LLM ---

MOCK_TOPICS = [
    SummaryTopic(
        topic="Bot 开发调试",
        time="10:24",
        contributors=["开发者", "测试员"],
        detail="大家讨论了如何为 Group Daily Analysis 插件添加 Debug 模式，以方便测试渲染效果而不消耗 Token。",
    ),
    SummaryTopic(
        topic="中午吃什么",
        time="11:50",
        contributors=["吃货A", "饿人B"],
        detail="围绕中午点外卖还是去食堂进行了激烈的讨论，最终决定去吃黄焖鸡米饭。",
    ),
]
MOCK_TITLES = [
    UserTitle(
        name="开发者",
        qq=10001,
        title="Debug 大师",
        reason="写了 100 行代码没有 Bug",
        message_count=86,
        personality="群里的代码洁癖患者，不管聊什么话题都能绕回技术，发言简短但信息密度极高。遇到 Bug 的第一反应是打开 IDE，遇到聚餐的第一反应是问有没有包厢带电源。",
    ),
    UserTitle(
        name="吃货A",
        qq=10002,
        title="干饭王",
        reason="三句话不离吃饭",
        message_count=52,
        personality="群里的美食雷达，每天上午10点准时开始讨论午饭，下午3点开始纠结晚饭。发言风格热情奔放，对食物的描述有一种让人血糖飙升的感染力。",
    ),
]
MOCK_QUOTES = [
    GoldenQuote(
        content="程序和人有一个能跑就行。",
        sender="运维小哥",
        reason="道出了 IT 行业的真谛",
    ),
    GoldenQuote(
        content="Bug 也是一种 Feature。",
        sender="产品经理",
        reason="重新定义了软件工程",
    ),
]

debug_analysis_cmd = on_command(
    "debug_daily_analysis",
    aliases={"日报调试"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@debug_analysis_cmd.handle()
async def handle_debug_analysis(bot: Bot, event: GroupMessageEvent):
    await debug_analysis_cmd.send("🧪 正在用 Mock 内容渲染日报（不调用 LLM）...")
    try:
        rows = await _recent_messages(event.group_id)
        stats, _ = await asyncio.to_thread(read_messages, event.group_id, rows)
        # 正式流程里 at 是按转写行查出来的，这里直接拼今天的时间
        now = datetime.now()
        topics = []
        for t in MOCK_TOPICS:
            hour, minute = map(int, t.time.split(":"))
            topics.append(
                t.model_copy(update={"at": now.replace(hour=hour, minute=minute)})
            )
        image = await render_report(
            AnalysisResult(
                statistics=stats,
                headline="能跑就行",
                topics=topics,
                user_titles=MOCK_TITLES,
                golden_quotes=MOCK_QUOTES,
            ),
            await _group_name(bot, event.group_id),
        )
    except Exception as e:
        logger.exception(f"日报调试渲染失败: {get_exc_desc(e)}")
        await debug_analysis_cmd.finish(f"日报调试渲染失败: {get_exc_desc(e)}")
    await debug_analysis_cmd.finish(image_segment(image))


# --- 定时推送 ---


async def auto_run_daily_analysis():
    try:
        bot = get_bot()
    except ValueError:
        logger.warning("未连接 Bot，跳过每日总结")
        return

    # 日报默认关，只推送给显式开启的群
    for group in await bot.get_group_list():
        group_id = group["group_id"]
        if not is_enabled("group_daily_analysis", str(group_id)):
            continue
        try:
            image = await build_report(bot, group_id)
            if image:
                await bot.send_group_msg(
                    group_id=group_id, message=image_segment(image)
                )
        except Exception as e:
            logger.exception(f"群 {group_id} 自动总结失败: {get_exc_desc(e)}")
        # 群与群之间错开
        await asyncio.sleep(10)


if plugin_config.enable_auto_analysis:
    hour, minute = plugin_config.auto_analysis_time.split(":")
    scheduler.add_job(
        auto_run_daily_analysis,
        "cron",
        hour=int(hour),
        minute=int(minute),
        id="group_daily_analysis_job",
        replace_existing=True,
    )
    logger.info(f"已注册每日总结定时任务: {plugin_config.auto_analysis_time}")
