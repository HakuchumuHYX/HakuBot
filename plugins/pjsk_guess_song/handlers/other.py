# pjsk_guess_song/handlers/other.py
"""
存放帮助等其他指令
"""
from pathlib import Path
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Bot

# [重构] 导入 image_service
from .. import image_service
# 导入辅助函数
from ..utils import _is_group_allowed
from ...plugin_manager.enable import *
from ...utils.common import create_exact_command_rule
# --- 帮助 ---
show_guess_song_help = on_command("猜歌帮助",
                                  priority=10,
                                  block=True,
                                  rule=create_exact_command_rule("猜歌帮助")
                                  )

@show_guess_song_help.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await show_guess_song_help.finish("猜歌功能在此群无法使用！")
            return
    if not await _is_group_allowed(event):
        return

    # [重构]
    img_path = await image_service.draw_help_image()
    if img_path:
        img_p = Path(img_path)
        await show_guess_song_help.send(MessageSegment.image(file=img_p.absolute().as_uri()))
    else:
        await show_guess_song_help.send("生成帮助图片时出错。")