# pjsk_guess_song/handlers/other.py
"""
资源版本、别名更新等其他指令
"""

from core.lifecycle import runtime
import json
from pathlib import Path
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import (
    GROUP,
    MessageEvent,
    Bot,
    GroupMessageEvent,
)

from plugins.pjsk_guess_song.runtime import (
    image_service,
    cache_service,
    resources_dir,
)
from plugins.pjsk_guess_song.tools.get_aliases import fetch_all_aliases
from core.access import switch_rule
from utils.onebot.rules import command_boundary
from utils.onebot.media import image_segment

# --- 资源版本 ---
show_resource_version = on_command(
    "猜歌资源",
    rule=command_boundary & switch_rule("pjsk_guess_song"),
    permission=GROUP,
    priority=5,
    block=True,
)


@show_resource_version.handle()
async def _(bot: Bot, event: MessageEvent):

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
    image = await image_service.draw_resource_version_image(
        stats, external_version_info
    )

    if image:
        await show_resource_version.finish(image_segment(image))
    else:
        await show_resource_version.finish(
            f"生成图片失败，请检查日志。\nDataVersion: {external_version_info}"
        )


# --- 获取别名 ---
fetch_aliases_cmd = on_command(
    "获取别名",
    rule=command_boundary & switch_rule("pjsk_guess_song"),
    permission=GROUP,
    priority=5,
    block=True,
)

# 简单的运行锁，防止重复触发
_alias_task_running = False


@fetch_aliases_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    global _alias_task_running

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
            logger.exception(f"后台获取别名任务出错: {e}")
        finally:
            _alias_task_running = False

    runtime.spawn(_run_fetch(), name="pjsk_guess_song")
