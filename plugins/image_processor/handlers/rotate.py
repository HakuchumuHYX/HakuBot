from nonebot import on_command, on_message, get_driver
from nonebot.rule import to_me
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import (
    MessageSegment,
    GroupMessageEvent,
    PrivateMessageEvent,
    Bot,
)
from nonebot.exception import FinishedException
from nonebot.log import logger
import asyncio
import aiohttp
from PIL import Image
import tempfile
import os
from plugins.image_processor.gif_reverse import reverse_gif, reverse_gif_alternative
from plugins.image_processor.image_cutout import remove_background
from plugins.image_processor.gif_speed import (
    change_gif_speed,
    change_gif_speed_alternative,
)
from plugins.image_processor.image_symmetry import process_image_symmetry
from plugins.image_processor.help import HELP
from utils.onebot.help import send_help
from plugins.image_processor.video_to_gif import convert_video_to_gif
from plugins.image_processor.image_mirror import process_image_mirror
from plugins.image_processor.image_rotate import process_image_rotate
from core.access import (
    get_status_override,
    get_plugin_feature_keys,
    sync_feature_statuses,
    is_plugin_enabled,
    set_plugin_status,
    is_feature_enabled,
    set_feature_status,
)
from core.cooldown import get_plugin_cd_duration, check_cd, update_cd, set_plugin_cd
from utils.onebot.media import image_segment
from utils.concurrency import run_in_pool
from utils.files import safe_delete_file
from plugins.image_processor.handlers.registration import (
    gif_reverse_handler,
    image_cutout_handler,
    gif_speed_handler,
    image_symmetry_handler,
    image_symmetry_left_handler,
    image_symmetry_right_handler,
    image_symmetry_center_handler,
    image_symmetry_top_handler,
    image_symmetry_bottom_handler,
    image_help_handler,
    video_to_gif_handler,
    image_mirror_handler,
    image_mirror_vertical_handler,
    image_rotate_handler,
    image_rotate_clockwise_handler,
    image_rotate_counter_handler,
)
from plugins.image_processor.runtime import _send_generated_image, is_gif_image


@image_rotate_handler.handle()
async def handle_rotate_default(event: Event, cmd_arg: Message = CommandArg()):
    """img旋转 [倍速] (默认顺时针)"""
    await handle_rotate_common(event, cmd_arg, "clockwise")


@image_rotate_clockwise_handler.handle()
async def handle_rotate_cw(event: Event, cmd_arg: Message = CommandArg()):
    """img顺时针 [倍速]"""
    await handle_rotate_common(event, cmd_arg, "clockwise")


@image_rotate_counter_handler.handle()
async def handle_rotate_ccw(event: Event, cmd_arg: Message = CommandArg()):
    """img逆时针 [倍速]"""
    await handle_rotate_common(event, cmd_arg, "counter_clockwise")


async def handle_rotate_common(event: Event, cmd_arg: Message, direction: str):
    """通用旋转处理逻辑"""
    user_id = str(event.user_id)
    group_id = (
        str(event.group_id) if isinstance(event, GroupMessageEvent) else "private"
    )

    # 1. 插件与功能开关检查
    if isinstance(event, GroupMessageEvent):
        # 使用 rotate 作为功能标识符
        if not is_feature_enabled("image_processor", "rotate", group_id, user_id):
            await image_rotate_handler.finish("旋转功能在本群无法使用！")
            return

        # CD 检查
        PLUGIN_ID = "image_processor:rotate"
        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_rotate_handler.finish(
                f"旋转功能还在冷却中，请等待 {remaining_cd} 秒"
            )
            return

    # 2. 参数解析 (提取倍速)
    args = cmd_arg.extract_plain_text().strip()
    speed = 1.0  # 默认 1 倍速
    if args:
        try:
            speed = float(args)
        except ValueError:
            # 如果参数不是数字，可能是用户输错了，或者是其他文本，这里忽略或提示
            pass

    # 限制倍速显示文本
    if speed > 5:
        await image_rotate_handler.send("转速太快啦！最高只能5倍速哦，已自动调整。")
    elif speed < 0.1:
        await image_rotate_handler.send("转速太慢啦！最低0.1倍速，已自动调整。")

    action_name = "顺时针旋转" if direction == "clockwise" else "逆时针旋转"

    # 3. 获取图片
    if not hasattr(event, "reply") or not event.reply:
        await image_rotate_handler.finish(f"请回复一条图片消息来使用{action_name}功能")

    image_found = False
    image_url = None
    for segment in event.reply.message:
        if segment.type == "image":
            url = segment.data.get("url", "")
            if url:
                image_found = True
                image_url = url
                break

    if not image_found or not image_url:
        await image_rotate_handler.finish("回复的消息中没有找到图片")

    # 4. 执行处理
    try:
        await image_rotate_handler.send(
            f"正在生成{action_name}动画 (倍速: {speed})，请稍候..."
        )

        result_path = await process_image_rotate(image_url, direction, speed)

        sent = await _send_generated_image(
            image_rotate_handler,
            result_path,
            f"{action_name}失败，生成文件异常",
            f"{action_name}处理失败",
        )
        if sent and isinstance(event, GroupMessageEvent):
            PLUGIN_ID = "image_processor:rotate"
            update_cd(PLUGIN_ID, group_id, user_id)

    except FinishedException:
        raise
    except Exception as e:
        await image_rotate_handler.send(f"处理出错: {str(e)}")
