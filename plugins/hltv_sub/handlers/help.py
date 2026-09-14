"""
帮助命令：hltv帮助 / hltv / hltvhelp
"""

from __future__ import annotations
from plugins.hltv_sub.help_content import build_help_sections

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from utils.onebot.media import image_segment
from plugins.hltv_sub.permissions import is_group_enabled
from plugins.hltv_sub.render import render_help


hltv_help = on_command("hltv帮助", aliases={"hltvhelp"}, priority=5, block=True)


@hltv_help.handle()
async def handle_hltv_help(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    img = await render_help(build_help_sections())
    await hltv_help.finish(image_segment(img))
