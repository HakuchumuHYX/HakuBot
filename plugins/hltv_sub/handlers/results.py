"""
结果列表命令：results列表
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from ..data_manager import data_manager
from ..data_source import hltv_data
from ..permissions import is_group_enabled
from ..render import render_results


results_list = on_command("results列表", aliases={"结果列表", "results"}, priority=5, block=True)


@results_list.handle()
async def handle_results_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events(group_id)
    if not subscriptions:
        await results_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return

    await results_list.send("正在获取比赛结果，请稍候...")

    try:
        results_by_event = {}

        for sub in subscriptions:
            results = await hltv_data.get_event_results(sub.event_id)
            if results:
                results_by_event[sub.event_title] = results

        if not results_by_event:
            await results_list.finish("暂无比赛结果")
            return

        img = await render_results(results_by_event)
        await results_list.finish(MessageSegment.image(img))

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取比赛结果失败: {e}")
        await results_list.finish("获取比赛结果失败，HLTV 可能暂时无法访问")
