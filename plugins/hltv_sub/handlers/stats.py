"""
比赛数据命令：stats / stats <match_id>
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

from utils.onebot.media import image_segment
from plugins.hltv_sub.data_manager import data_manager
from plugins.hltv_sub.data_source import hltv_data
from plugins.hltv_sub.http_client import HLTVFetchError
from plugins.hltv_sub.permissions import is_group_enabled
from plugins.hltv_sub.render import render_stats


stats_cmd = on_command("stats", priority=5, block=True)


@stats_cmd.handle()
async def handle_stats(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    match_id = args.extract_plain_text().strip()
    subscriptions = data_manager.get_subscribed_events()

    if not match_id:
        # 获取最新比赛数据
        if not subscriptions:
            await stats_cmd.finish("请先订阅赛事，或提供比赛ID\n例如：stats 2370931")
            return

        await stats_cmd.send("正在获取最新比赛数据...")

        try:
            for sub in subscriptions:
                stats = await hltv_data.get_latest_result_with_stats(
                    sub.event_id, sub.event_title
                )
                if stats:
                    img = await render_stats(stats)
                    await stats_cmd.finish(image_segment(img))
                    return

            await stats_cmd.finish("暂无比赛数据")

        except FinishedException:
            raise
        except HLTVFetchError as e:
            await stats_cmd.finish(str(e))
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")

    else:
        # 获取指定比赛数据
        await stats_cmd.send(f"正在获取比赛 #{match_id} 的数据...")

        try:
            stats = await hltv_data.get_match_stats(match_id=match_id)

            if stats:
                img = await render_stats(stats)
                await stats_cmd.finish(image_segment(img))
            else:
                await stats_cmd.finish(f"无法获取比赛 #{match_id} 的数据")

        except FinishedException:
            raise
        except HLTVFetchError as e:
            await stats_cmd.finish(str(e))
        except Exception as e:
            logger.error(f"获取比赛数据失败: {e}")
            await stats_cmd.finish("获取比赛数据失败，HLTV 可能暂时无法访问")
