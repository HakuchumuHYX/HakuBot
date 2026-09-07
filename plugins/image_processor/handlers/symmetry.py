from utils.onebot.messages import reply_image_segments
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


@image_symmetry_handler.handle()
@image_symmetry_left_handler.handle()
async def handle_image_symmetry_left(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片左对称"""
    await handle_image_symmetry_common(event, "left")


@image_symmetry_right_handler.handle()
async def handle_image_symmetry_right(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片右对称"""
    await handle_image_symmetry_common(event, "right")


@image_symmetry_center_handler.handle()
async def handle_image_symmetry_center(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片中心对称"""
    await handle_image_symmetry_common(event, "center")


@image_symmetry_top_handler.handle()
async def handle_image_symmetry_top(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片上对称"""
    await handle_image_symmetry_common(event, "top")


@image_symmetry_bottom_handler.handle()
async def handle_image_symmetry_bottom(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片下对称"""
    await handle_image_symmetry_common(event, "bottom")


async def handle_image_symmetry_common(event: Event, symmetry_type: str):
    """通用的对称处理函数"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled(
            "image_processor", "symmetry", str(event.group_id), user_id
        ):
            await image_symmetry_handler.finish("对称功能在本群无法使用！")
            return

    if isinstance(event, GroupMessageEvent):
        PLUGIN_ID = "image_processor:symmetry"  # 对应注册表中的功能 ID
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_symmetry_handler.finish(
                f"对称功能还在冷却中，请等待 {remaining_cd} 秒"
            )
            return

    symmetry_names = {
        "left": "左对称",
        "right": "右对称",
        "center": "中心对称",
        "top": "上对称",
        "bottom": "下对称",
    }

    symmetry_name = symmetry_names.get(symmetry_type, "对称")

    # 检查是否为回复消息
    if not hasattr(event, "reply"):
        await image_symmetry_handler.finish(
            f"请回复一条图片消息来使用{symmetry_name}功能"
        )

    reply = event.reply
    if not reply:
        await image_symmetry_handler.finish(
            f"请回复一条图片消息来使用{symmetry_name}功能"
        )

    # 获取图片消息
    image_found = False
    image_url = None

    for segment in reply_image_segments(event):
        url = segment.data.get("url", "")
        if url:
            image_found = True
            image_url = url
            break

    if not image_found or not image_url:
        await image_symmetry_handler.finish("回复的消息中没有找到图片")

    try:
        # 确保发送处理中消息
        try:
            await image_symmetry_handler.send(f"正在处理图片{symmetry_name}，请稍候...")
        except Exception as send_error:
            logger.error(f"发送处理中消息失败: {send_error}")
            # 继续处理，不因为发送失败而中断

        # 直接异步调用对称处理函数
        result_path = await process_image_symmetry(image_url, symmetry_type)

        sent = await _send_generated_image(
            image_symmetry_handler,
            result_path,
            f"图片{symmetry_name}处理失败，生成的文件异常",
            f"图片{symmetry_name}处理失败",
        )
        if sent and isinstance(event, GroupMessageEvent):
            PLUGIN_ID = "image_processor:symmetry"
            group_id = str(event.group_id)
            user_id = str(event.user_id)
            update_cd(PLUGIN_ID, group_id, user_id)
    except FinishedException:
        raise
    except asyncio.TimeoutError:
        await image_symmetry_handler.send(f"图片{symmetry_name}处理超时，请稍后重试")
    except Exception as e:
        await image_symmetry_handler.send(f"处理图片时出错: {str(e)}")
