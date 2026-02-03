"""
帮助命令：hltv帮助 / hltv / hltvhelp
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from ..permissions import is_group_enabled


hltv_help = on_command("hltv帮助", aliases={"hltv", "hltvhelp"}, priority=5, block=True)


@hltv_help.handle()
async def handle_hltv_help(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    help_msg = """📖 HLTV 订阅插件帮助

【赛事相关】
• event列表 - 查看近期大型赛事
• event订阅 [ID] - 订阅指定赛事
• event取消订阅 [ID] - 取消订阅
• 我的订阅 - 查看已订阅的赛事

【比赛相关】
• matches列表 - 查看已订阅赛事的比赛
• results列表 - 查看已订阅赛事的结果
• stats - 查看最新比赛数据
• stats [ID] - 查看指定比赛数据

【管理命令】
• hltv开启 - 开启本群功能
• hltv关闭 - 关闭本群功能"""

    await hltv_help.finish(help_msg)
