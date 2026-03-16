"""
比赛数据命令：stats / stats <match_id>
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

from ..data_manager import data_manager
from ..data_source import hltv_data
from ..permissions import is_group_enabled
from ..render import render_stats


stats_cmd = on_command("stats", aliases={"比赛数据", "数据"}, priority=5, block=True)


@stats_cmd.handle()
async def handle_stats(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    match_id = args.extract_plain_text().strip()
    subscriptions = data_manager.get_subscribed_events(group_id)

    if not match_id:
        # 获取最新比赛数据
        if not subscriptions:
            await stats_cmd.finish("请先订阅赛事，或提供比赛ID\n例如：stats 2370931")
            return

        await stats_cmd.send("正在获取最新比赛数据...")

        try:
            for sub in subscriptions:
                stats = await hltv_data.get_latest_result_with_stats(sub.event_id, sub.event_title)
                if stats:
                    img = await render_stats(stats)
                    await stats_cmd.finish(MessageSegment.image(img))
                    return

            await stats_cmd.finish("暂无比赛数据")

        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")

    else:
        # 获取指定比赛数据
        await stats_cmd.send(f"正在获取比赛 #{match_id} 的数据...")

        try:
            # 优先直接按 match_id 拉取，避免先遍历所有订阅赛事 results 带来的额外请求开销
            stats = await hltv_data.get_match_stats(match_id=match_id)

            # 直连失败时，再尝试通过订阅赛事补全 slug 信息后重试（兼容少量边缘路由）
            if not stats and subscriptions:
                team1 = ""
                team2 = ""
                event_title = ""

                for sub in subscriptions:
                    results = await hltv_data.get_event_results(sub.event_id, max_results=10)
                    for r in results:
                        if r.id == match_id:
                            team1 = r.team1
                            team2 = r.team2
                            event_title = sub.event_title
                            break
                    if team1:
                        break

                stats = await hltv_data.get_match_stats(
                    match_id=match_id,
                    team1=team1,
                    team2=team2,
                    event_title=event_title,
                )

            if stats:
                img = await render_stats(stats)
                await stats_cmd.finish(MessageSegment.image(img))
            else:
                await stats_cmd.finish(f"无法获取比赛 #{match_id} 的数据")

        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")
