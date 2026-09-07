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


@image_mirror_handler.handle()
async def handle_image_mirror_horizontal(event: Event):
    """处理水平镜像（默认）"""
    # 传入 "horizontal" 作为方向
    await handle_image_mirror_common(event, "horizontal")


@image_mirror_vertical_handler.handle()
async def handle_image_mirror_vertical(event: Event):
    """处理垂直镜像"""
    # 传入 "vertical" 作为方向
    await handle_image_mirror_common(event, "vertical")


async def handle_image_mirror_common(event: Event, direction: str):
    """通用的镜像处理函数"""
    user_id = str(event.user_id)
    group_id = (
        str(event.group_id) if isinstance(event, GroupMessageEvent) else "private"
    )

    # 插件开关与权限检查
    if isinstance(event, GroupMessageEvent):
        # 使用 mirror 作为功能标识符
        if not is_feature_enabled("image_processor", "mirror", group_id, user_id):
            await image_mirror_handler.finish("镜像功能在本群无法使用！")
            return

        PLUGIN_ID = "image_processor:mirror"
        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_mirror_handler.finish(
                f"镜像功能还在冷却中，请等待 {remaining_cd} 秒"
            )
            return

    # 定义中文名称
    direction_names = {"horizontal": "水平镜像", "vertical": "垂直镜像"}
    action_name = direction_names.get(direction, "镜像")

    # 检查回复
    if not hasattr(event, "reply") or not event.reply:
        await image_mirror_handler.finish(f"请回复一条图片消息来使用{action_name}功能")

    # 获取图片
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
        await image_mirror_handler.finish("回复的消息中没有找到图片")

    try:
        await image_mirror_handler.send(f"正在处理图片{action_name}，请稍候...")

        result_path = await process_image_mirror(image_url, direction)

        sent = await _send_generated_image(
            image_mirror_handler,
            result_path,
            f"图片{action_name}处理失败，文件异常",
            f"图片{action_name}处理失败",
        )
        if sent and isinstance(event, GroupMessageEvent):
            PLUGIN_ID = "image_processor:mirror"
            update_cd(PLUGIN_ID, group_id, user_id)

    except FinishedException:
        raise
    except Exception as e:
        await image_mirror_handler.send(f"处理图片时出错: {str(e)}")
