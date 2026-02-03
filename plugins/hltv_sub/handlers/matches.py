"""
比赛列表命令：matches列表
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from ..data_manager import data_manager
from ..data_source import hltv_data
from ..permissions import is_group_enabled
from ..render import render_matches


matches_list = on_command("matches列表", aliases={"比赛列表", "matches"}, priority=5, block=True)


@matches_list.handle()
async def handle_matches_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    subscriptions = data_manager.get_subscribed_events(group_id)
    if not subscriptions:
        await matches_list.finish("请先订阅赛事\n使用 event列表 查看可订阅的赛事")
        return

    await matches_list.send("正在获取比赛列表，请稍候...")

    try:
        matches_by_event = {}
        live_count = 0
        upcoming_count = 0

        for sub in subscriptions:
            matches = await hltv_data.get_event_matches(sub.event_id)

            if matches:
                matches_by_event[sub.event_title] = matches
                for m in matches:
                    if m.is_live:
                        live_count += 1
                    else:
                        upcoming_count += 1

        if not matches_by_event:
            await matches_list.finish("暂无比赛")
            return

        img = await render_matches(matches_by_event, live_count, upcoming_count)
        await matches_list.finish(MessageSegment.image(img))

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"获取比赛列表失败: {e}")
        await matches_list.finish("获取比赛列表失败，HLTV 可能暂时无法访问")
