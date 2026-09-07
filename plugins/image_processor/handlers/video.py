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


@video_to_gif_handler.handle()
async def handle_video_to_gif(
    event: Event, bot: Bot, cmd_arg: Message = CommandArg()
):  # 移除默认值，使用依赖注入
    """处理视频转GIF"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled(
            "image_processor", "video_to_gif", str(event.group_id), user_id
        ):
            await video_to_gif_handler.finish("视频转GIF功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, "reply"):
        await video_to_gif_handler.finish("请回复一条视频消息来使用视频转GIF功能")

    reply = event.reply
    if not reply:
        await video_to_gif_handler.finish("请回复一条视频消息来使用视频转GIF功能")

    # 获取 access_token
    access_token = None
    try:
        # 通过 bot 实例获取 access_token
        access_token = bot.config.access_token
        if access_token:
            logger.info("成功获取 Access Token")
    except Exception as e:
        logger.warning(
            f"警告: 获取 Access Token 失败: {e} (如果OneBot实现未配置Token则此项可选)"
        )

    # 获取视频消息 - 增强检测逻辑
    video_found = False
    video_url = None
    video_file_name = None
    expected_file_size = 0
    file_id = None

    logger.info(f"开始检测回复消息中的视频...")
    logger.info(f"回复消息内容: {reply.message}")

    # 遍历所有消息段，查找视频或文件
    for segment in reply.message:
        logger.info(f"检查消息段: type={segment.type}, data={segment.data}")

        # 情况1: 直接视频消息
        if segment.type == "video":
            url = segment.data.get("url", "")
            file_name = segment.data.get("file", "")
            file_size_str = segment.data.get("file_size", "0")
            logger.info(
                f"找到视频消息段: url={url[:100] if url else 'None'}, file={file_name}"
            )

            try:
                expected_file_size = int(file_size_str)
            except ValueError:
                expected_file_size = 0

            if url:
                video_found = True
                video_url = url
                video_file_name = file_name
                break  # 视频消息优先

        # 情况2: 文件消息（群文件形式）
        elif segment.type == "file":
            file_name = segment.data.get("file", "")
            url = segment.data.get("url", "")
            _file_id = segment.data.get("file_id", "")
            file_size = segment.data.get("file_size", "")

            logger.info(
                f"找到文件消息段: file={file_name}, url={url[:100] if url else 'None'}, file_id={_file_id}, file_size={file_size}"
            )

            # 检查是否为视频文件
            if file_name and any(
                file_name.lower().endswith(ext)
                for ext in [".mp4", ".avi", ".mov", ".webm", ".mkv", ".flv", ".wmv"]
            ):
                logger.info(f"检测到视频文件: {file_name}")
                video_found = True
                video_file_name = file_name

                try:
                    expected_file_size = int(file_size)
                except ValueError:
                    expected_file_size = 0

                if _file_id:
                    file_id = _file_id
                    # 此时不 break，优先使用 file_id

                if url and not file_id:
                    video_url = url

    if file_id and isinstance(event, GroupMessageEvent):
        logger.info(
            f"检测到群文件 file_id: {file_id}，尝试调用 OneBot API 获取下载链接..."
        )
        try:
            api_response = await bot.call_api(
                "get_group_file_url", group_id=event.group_id, file_id=file_id
            )
            new_url = api_response.get("url")
            if not new_url:
                raise Exception("API call 'get_group_file_url' did not return a URL.")

            video_url = new_url
            video_found = True
            logger.info(f"成功获取 Go-CQHTTP 代理 URL: {video_url[:200]}...")
        except Exception as e:
            logger.error(f"调用 get_group_file_url 失败: {e}")
            await video_to_gif_handler.finish(
                f"获取群文件下载链接失败: {e}。请确保Bot具有群文件权限。"
            )

    elif video_found and not video_url:
        if video_file_name:
            await video_to_gif_handler.finish(
                f"检测到视频文件 '{video_file_name}'，但无法获取下载链接。请确保视频文件可通过URL直接访问。"
            )
        else:
            await video_to_gif_handler.finish(
                "检测到视频但无法获取下载信息，请联系管理员检查配置。"
            )

    if not video_found or not video_url:
        error_msg = "回复的消息中没有找到视频，请确保回复的是视频消息。"
        error_msg += "\n支持格式: MP4, AVI, MOV, WebM, MKV, FLV, WMV"
        await video_to_gif_handler.finish(error_msg)

    try:
        await video_to_gif_handler.send(
            "正在处理视频转GIF，这可能需要一些时间，请稍候..."
        )

        result_path = await convert_video_to_gif(
            video_url, video_file_name, expected_file_size, access_token
        )

        await _send_generated_image(
            video_to_gif_handler,
            result_path,
            "生成的GIF文件异常，处理失败",
            "视频转GIF处理失败",
        )
        return

    except FinishedException:
        raise
    except Exception as e:
        await video_to_gif_handler.finish(f"处理视频时出错: {str(e)}")
