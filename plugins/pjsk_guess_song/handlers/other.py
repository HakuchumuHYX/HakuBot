# pjsk_guess_song/handlers/other.py
"""
存放帮助等其他指令
"""
import json
from pathlib import Path
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Bot, GroupMessageEvent

from .. import image_service, cache_service
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

    img_path = await image_service.draw_help_image()
    if img_path:
        img_p = Path(img_path)
        await show_guess_song_help.send(MessageSegment.image(file=img_p.absolute().as_uri()))
    else:
        await show_guess_song_help.send("生成帮助图片时出错。")


# --- 资源版本 ---
show_resource_version = on_command("猜歌资源",
                                   priority=10,
                                   block=True,
                                   rule=create_exact_command_rule("猜歌资源")
                                   )


@show_resource_version.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await show_guess_song_help.finish("猜歌功能在此群无法使用！")
            return
    if not await _is_group_allowed(event):
        return

    # 1. 统计当前加载的资源
    stats = {
        "song_count": len(cache_service.song_data),
        "piano_count": len(cache_service.available_piano_songs),
        "acc_count": len(cache_service.available_accompaniment_songs),
        "vocal_count": len(cache_service.available_vocals_songs),
        "bass_count": len(cache_service.available_bass_songs),
        "drums_count": len(cache_service.available_drums_songs)
    }

    # 2. 读取外部数据版本
    target_json_path = Path(r"E:\Download\bot\haruki-sekai-master\versions\current_version.json")

    external_version_info = "未知 (文件未找到)"

    # 尝试读取文件
    found_path = None
    if target_json_path.exists():
        found_path = target_json_path
    else:
        # 回退逻辑：尝试相对路径
        relative_path = Path("..") / "haruki-sekai-master" / "versions" / "current_version.json"
        if relative_path.exists():
            found_path = relative_path

    if found_path:
        try:
            with open(found_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                external_version_info = data.get('dataVersion', '未知 (字段缺失)')
        except Exception as e:
            external_version_info = f"读取错误: {e}"

    # 3. 生成图片并发送
    img_path = await image_service.draw_resource_version_image(stats, external_version_info)

    if img_path:
        img_p = Path(img_path)
        await show_resource_version.finish(MessageSegment.image(file=img_p.absolute().as_uri()))
    else:
        await show_resource_version.finish(f"生成图片失败，请检查日志。\nDataVersion: {external_version_info}")
