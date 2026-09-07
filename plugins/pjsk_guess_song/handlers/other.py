# pjsk_guess_song/handlers/other.py
"""
存放帮助等其他指令
"""

from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown
import asyncio
import json
from pathlib import Path
from nonebot import on_command
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    MessageSegment,
    Bot,
    GroupMessageEvent,
)

from plugins.pjsk_guess_song.runtime import (
    image_service,
    cache_service,
    resources_dir,
    game_service,
)
from plugins.pjsk_guess_song.help_content import build_help_document
from utils.onebot.help import send_help
from plugins.pjsk_guess_song.tools.get_aliases import fetch_all_aliases
from plugins.pjsk_guess_song.utils import _is_group_allowed
from core.access import (
    get_status_override,
    get_plugin_feature_keys,
    sync_feature_statuses,
    is_plugin_enabled,
    set_plugin_status,
    is_feature_enabled,
    set_feature_status,
)
from utils.onebot.rules import create_exact_command_rule
from utils.onebot.media import image_segment

# --- 帮助 ---
show_guess_song_help = on_command(
    "猜歌帮助", priority=10, block=True, rule=create_exact_command_rule("猜歌帮助")
)


@show_guess_song_help.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await show_guess_song_help.finish()
            return
    if not await _is_group_allowed(event):
        return

    await send_help(show_guess_song_help, build_help_document(game_service))


# --- 资源版本 ---
show_resource_version = on_command(
    "猜歌资源", priority=10, block=True, rule=create_exact_command_rule("猜歌资源")
)


@show_resource_version.handle()
async def _(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await show_guess_song_help.finish()
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
        "drums_count": len(cache_service.available_drums_songs),
    }

    # 2. 读取外部数据版本
    target_json_path = (
        Path("..") / "haruki-sekai-master" / "versions" / "current_version.json"
    )

    external_version_info = "未知 (文件未找到)"

    # 尝试读取文件
    found_path = None
    if target_json_path.exists():
        found_path = target_json_path
    else:
        # 回退逻辑：尝试相对路径
        relative_path = (
            Path("..") / "haruki-sekai-master" / "versions" / "current_version.json"
        )
        if relative_path.exists():
            found_path = relative_path

    if found_path:
        try:
            with open(found_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                external_version_info = data.get("dataVersion", "未知 (字段缺失)")
        except Exception as e:
            external_version_info = f"读取错误: {e}"

    # 3. 生成图片并发送
    img_path = await image_service.draw_resource_version_image(
        stats, external_version_info
    )

    if img_path:
        img_p = Path(img_path)
        await show_resource_version.finish(image_segment(img_p))
    else:
        await show_resource_version.finish(
            f"生成图片失败，请检查日志。\nDataVersion: {external_version_info}"
        )


# --- 获取别名 ---
fetch_aliases_cmd = on_command(
    "获取别名", priority=10, block=True, rule=create_exact_command_rule("获取别名")
)

# 简单的运行锁，防止重复触发
_alias_task_running = False


@fetch_aliases_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    global _alias_task_running

    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await fetch_aliases_cmd.finish()
            return
    if not await _is_group_allowed(event):
        return

    if _alias_task_running:
        await fetch_aliases_cmd.finish("别名获取任务正在运行中，请稍后再试。")
        return

    _alias_task_running = True
    await fetch_aliases_cmd.send("开始在后台获取歌曲别名，完成后会通知你。")

    async def _run_fetch():
        global _alias_task_running
        try:
            guess_song_path = str(resources_dir / "guess_song.json")
            aliases_output_path = str(resources_dir / "song_aliases.json")
            success = await fetch_all_aliases(guess_song_path, aliases_output_path)
            if success:
                cache_service._load_song_aliases()
                msg = "歌曲别名获取完成，已重新加载。"
            else:
                msg = "歌曲别名获取失败，请查看日志。"

            try:
                if isinstance(event, GroupMessageEvent):
                    await bot.send_group_msg(group_id=event.group_id, message=msg)
                else:
                    await bot.send_private_msg(user_id=event.user_id, message=msg)
            except Exception as e:
                logger.warning(f"发送别名获取结果通知失败: {e}")
        except Exception as e:
            logger.error(f"后台获取别名任务出错: {e}", exc_info=True)
        finally:
            _alias_task_running = False

    runtime.spawn(_run_fetch(), name="pjsk_guess_song")
