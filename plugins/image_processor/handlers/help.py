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


@image_help_handler.handle()
async def handle_image_help(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片处理帮助"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await image_help_handler.finish()
            return

    await send_help(image_help_handler, HELP)
