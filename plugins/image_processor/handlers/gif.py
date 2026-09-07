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


@gif_reverse_handler.handle()
async def handle_gif_reverse(event: Event, cmd_arg: Message = CommandArg()):
    """处理GIF倒放"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled(
            "image_processor", "reverse", str(event.group_id), user_id
        ):
            await gif_reverse_handler.finish("gif倒放功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, "reply"):
        await gif_reverse_handler.finish("请回复一条GIF消息来使用倒放功能")

    reply = event.reply
    if not reply:
        await gif_reverse_handler.finish("请回复一条GIF消息来使用倒放功能")

    # 获取图片消息
    gif_found = False
    gif_url = None

    for segment in reply_image_segments(event):
        url = segment.data.get("url", "")
        file_name = segment.data.get("file", "")
        if url:
            # 使用改进的GIF检测
            if await is_gif_image(url) or "gif" in file_name.lower():
                gif_found = True
                gif_url = url
                break

    if not gif_found or not gif_url:
        await gif_reverse_handler.finish(
            "回复的消息中没有找到GIF图片，请确保回复的是GIF格式"
        )

    try:
        # 处理GIF倒放
        await gif_reverse_handler.send("正在处理GIF倒放，请稍候...")

        # 首先尝试主方法
        result_path = await reverse_gif(gif_url)

        # 如果主方法失败，尝试备选方案
        if not result_path or not os.path.exists(result_path):
            await gif_reverse_handler.send("主方法失败，尝试备选方案...")
            result_path = await run_in_pool(reverse_gif_alternative, gif_url)

        await _send_generated_image(
            gif_reverse_handler,
            result_path,
            "生成的GIF文件异常，处理失败",
            "GIF倒放处理失败，请确保图片是有效的GIF格式",
        )
        return
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        await gif_reverse_handler.finish(f"处理GIF时出错: {str(e)}")


@gif_speed_handler.handle()
async def handle_gif_speed(event: Event, cmd_arg: Message = CommandArg()):
    """处理GIF倍速播放"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled(
            "image_processor", "speed", str(event.group_id), user_id
        ):
            await gif_speed_handler.finish("gif加速功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, "reply"):
        await gif_speed_handler.finish("请回复一条GIF消息来使用倍速功能")

    reply = event.reply
    if not reply:
        await gif_speed_handler.finish("请回复一条GIF消息来使用倍速功能")

    # 获取倍速参数
    args = cmd_arg.extract_plain_text().strip()
    if not args:
        await gif_speed_handler.finish("请指定倍速倍数，例如：imgx 2")

    try:
        speed_factor = float(args)
        if speed_factor <= 0:
            await gif_speed_handler.finish("倍速倍数必须大于0")
    except ValueError:
        await gif_speed_handler.finish("请输入有效的数字作为倍速倍数")

    # 限制最大倍速为5
    if speed_factor > 5:
        speed_factor = 5.0
        await gif_speed_handler.send("倍速倍数最高为5，已自动调整为5倍速")

    # 获取图片消息
    gif_found = False
    gif_url = None

    for segment in reply_image_segments(event):
        url = segment.data.get("url", "")
        file_name = segment.data.get("file", "")
        if url:
            if await is_gif_image(url) or "gif" in file_name.lower():
                gif_found = True
                gif_url = url
                break

    if not gif_found or not gif_url:
        await gif_speed_handler.finish(
            "回复的消息中没有找到GIF图片，请确保回复的是GIF格式"
        )

    try:
        await gif_speed_handler.send(f"正在处理GIF {speed_factor} 倍速，请稍候...")

        # 首先尝试主方法
        result_path = await change_gif_speed(gif_url, speed_factor)

        # 如果主方法失败，尝试备选方案
        if not result_path or not os.path.exists(result_path):
            await gif_speed_handler.send("主方法失败，尝试备选方案...")
            result_path = await run_in_pool(
                change_gif_speed_alternative, gif_url, speed_factor
            )

        await _send_generated_image(
            gif_speed_handler,
            result_path,
            "生成的GIF文件异常，处理失败",
            "GIF倍速处理失败，请确保图片是有效的GIF格式",
        )
        return
    except FinishedException:
        raise
    except Exception as e:
        await gif_speed_handler.finish(f"处理GIF时出错: {str(e)}")
