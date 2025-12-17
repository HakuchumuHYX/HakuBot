from nonebot import on_command, on_message, get_driver
from nonebot.rule import to_me
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent, PrivateMessageEvent, Bot
from nonebot.exception import FinishedException
from nonebot.log import logger
import asyncio
import aiohttp
from PIL import Image
import tempfile
import os

from .gif_reverse import reverse_gif, reverse_gif_alternative
from .image_cutout import remove_background
from .gif_speed import change_gif_speed, change_gif_speed_alternative
from .image_symmetry import process_image_symmetry
from .help import generate_help_image, get_help_text
from .video_to_gif import convert_video_to_gif
from .image_mirror import process_image_mirror
from .image_rotate import process_image_rotate

from ..plugin_manager.enable import *
from ..plugin_manager.cd_manager import *


async def download_and_check_gif(url: str) -> bool:
    """下载并检查是否为GIF"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    # 只下载前几KB来判断文件类型
                    content = await response.content.read(1024)

                    # 检查GIF文件头
                    if content.startswith(b'GIF8'):
                        return True

                    # 检查Content-Type
                    content_type = response.headers.get('Content-Type', '')
                    if 'gif' in content_type.lower():
                        return True

                    # 检查文件扩展名
                    if any(url.lower().endswith(ext) for ext in ['.gif', '.gif?', '.gif&']):
                        return True
        return False
    except Exception as e:
        logger.error(f"GIF检测错误: {e}")
        # 如果检测失败，假设是GIF让用户尝试
        return True


async def is_gif_image(url: str) -> bool:
    """更可靠的GIF检测"""
    # 首先检查URL中的扩展名
    if any(url.lower().endswith(ext) for ext in ['.gif', '.gif?', '.gif&']):
        return True

    # 检查文件路径（如果有）
    if 'gif' in url.lower():
        return True

    # 最后下载验证
    return await download_and_check_gif(url)


gif_reverse_handler = on_command("img倒放", rule=to_me(), priority=5, block=True)
image_cutout_handler = on_command("imgcut", rule=to_me(), priority=5, block=True)
gif_speed_handler = on_command("imgx", rule=to_me(), priority=5, block=True)
image_symmetry_handler = on_command("img对称", rule=to_me(), priority=5, block=True)
image_symmetry_left_handler = on_command("img左对称", rule=to_me(), priority=5, block=True)
image_symmetry_right_handler = on_command("img右对称", rule=to_me(), priority=5, block=True)
image_symmetry_center_handler = on_command("img中心对称", rule=to_me(), priority=5, block=True)
image_symmetry_top_handler = on_command("img上对称", rule=to_me(), priority=5, block=True)
image_symmetry_bottom_handler = on_command("img下对称", rule=to_me(), priority=5, block=True)
image_help_handler = on_command("imghelp", priority=5, block=True)
video_to_gif_handler = on_command("imggif", rule=to_me(), priority=5, block=True)
image_mirror_handler = on_command("img镜像", rule=to_me(), priority=5, block=True)
image_mirror_vertical_handler = on_command("img上镜像", rule=to_me(), priority=5, block=True)
image_rotate_handler = on_command("img旋转", rule=to_me(), priority=5, block=True)
image_rotate_clockwise_handler = on_command("img顺时针", rule=to_me(), priority=5, block=True)
image_rotate_counter_handler = on_command("img逆时针", rule=to_me(), priority=5, block=True)

@gif_reverse_handler.handle()
async def handle_gif_reverse(event: Event, cmd_arg: Message = CommandArg()):
    """处理GIF倒放"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await gif_reverse_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "reverse", str(event.group_id), user_id):
            await image_cutout_handler.finish("gif倒放功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, 'reply'):
        await gif_reverse_handler.finish("请回复一条GIF消息来使用倒放功能")

    reply = event.reply
    if not reply:
        await gif_reverse_handler.finish("请回复一条GIF消息来使用倒放功能")

    # 获取图片消息
    gif_found = False
    gif_url = None

    for segment in reply.message:
        if segment.type == "image":
            url = segment.data.get("url", "")
            file_name = segment.data.get("file", "")
            if url:
                # 使用改进的GIF检测
                if await is_gif_image(url) or 'gif' in file_name.lower():
                    gif_found = True
                    gif_url = url
                    break

    if not gif_found or not gif_url:
        await gif_reverse_handler.finish("回复的消息中没有找到GIF图片，请确保回复的是GIF格式")

    try:
        # 处理GIF倒放
        await gif_reverse_handler.send("正在处理GIF倒放，请稍候...")

        # 首先尝试主方法
        result_path = await reverse_gif(gif_url)

        # 如果主方法失败，尝试备选方案
        if not result_path or not os.path.exists(result_path):
            await gif_reverse_handler.send("主方法失败，尝试备选方案...")
            result_path = reverse_gif_alternative(gif_url)

        if result_path and os.path.exists(result_path):
            # 读取文件大小，避免发送空文件
            file_size = os.path.getsize(result_path)
            if file_size > 100:  # 确保文件不是空的
                await gif_reverse_handler.finish(MessageSegment.image(f"file:///{result_path}"))
            else:
                await gif_reverse_handler.finish("生成的GIF文件异常，处理失败")
        else:
            await gif_reverse_handler.finish("GIF倒放处理失败，请确保图片是有效的GIF格式")
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        await gif_reverse_handler.finish(f"处理GIF时出错: {str(e)}")


@image_cutout_handler.handle()
async def handle_image_cutout(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片抠图"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await image_cutout_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "cutout", str(event.group_id), user_id):
            await image_cutout_handler.finish("抠图功能在本群无法使用！")
            return

    if not hasattr(event, 'reply'):
        await image_cutout_handler.finish("请回复一条图片消息来使用抠图功能")

    reply = event.reply
    if not reply:
        await image_cutout_handler.finish("请回复一条图片消息来使用抠图功能")

    # 获取图片消息
    image_found = False
    image_url = None

    for segment in reply.message:
        if segment.type == "image":
            url = segment.data.get("url", "")
            if url:
                image_found = True
                image_url = url
                break

    if not image_found or not image_url:
        await image_cutout_handler.finish("回复的消息中没有找到图片")

    try:
        await image_cutout_handler.send("正在处理图片抠图，请稍候...")
        result_path = await remove_background(image_url)
        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                await image_cutout_handler.finish(MessageSegment.image(f"file:///{result_path}"))
            else:
                await image_cutout_handler.finish("抠图处理失败，生成的文件异常")
        else:
            await image_cutout_handler.finish("图片抠图处理失败")
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        await image_cutout_handler.finish(f"处理图片时出错: {str(e)}")


@gif_speed_handler.handle()
async def handle_gif_speed(event: Event, cmd_arg: Message = CommandArg()):
    """处理GIF倍速播放"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await gif_speed_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "speed", str(event.group_id), user_id):
            await gif_speed_handler.finish("gif加速功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, 'reply'):
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

    for segment in reply.message:
        if segment.type == "image":
            url = segment.data.get("url", "")
            file_name = segment.data.get("file", "")
            if url:
                if await is_gif_image(url) or 'gif' in file_name.lower():
                    gif_found = True
                    gif_url = url
                    break

    if not gif_found or not gif_url:
        await gif_speed_handler.finish("回复的消息中没有找到GIF图片，请确保回复的是GIF格式")

    try:
        await gif_speed_handler.send(f"正在处理GIF {speed_factor} 倍速，请稍候...")

        # 首先尝试主方法
        result_path = await change_gif_speed(gif_url, speed_factor)

        # 如果主方法失败，尝试备选方案
        if not result_path or not os.path.exists(result_path):
            await gif_speed_handler.send("主方法失败，尝试备选方案...")
            result_path = change_gif_speed_alternative(gif_url, speed_factor)

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                await gif_speed_handler.finish(MessageSegment.image(f"file:///{result_path}"))
            else:
                await gif_speed_handler.finish("生成的GIF文件异常，处理失败")
        else:
            await gif_speed_handler.finish("GIF倍速处理失败，请确保图片是有效的GIF格式")
    except FinishedException:
        raise
    except Exception as e:
        await gif_speed_handler.finish(f"处理GIF时出错: {str(e)}")


# 图片对称处理（左对称/默认对称）
@image_symmetry_handler.handle()
@image_symmetry_left_handler.handle()
async def handle_image_symmetry_left(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片左对称"""
    await handle_image_symmetry_common(event, "left")


# 图片右对称处理
@image_symmetry_right_handler.handle()
async def handle_image_symmetry_right(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片右对称"""
    await handle_image_symmetry_common(event, "right")


# 图片中心对称处理
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
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await image_symmetry_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "symmetry", str(event.group_id), user_id):
            await image_symmetry_handler.finish("对称功能在本群无法使用！")
            return

    if isinstance(event, GroupMessageEvent):
        PLUGIN_ID = "image_processor:symmetry"  # 对应 readme.md 中的功能ID
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_symmetry_handler.finish(f"对称功能还在冷却中，请等待 {remaining_cd} 秒")
            return

    symmetry_names = {
        "left": "左对称",
        "right": "右对称",
        "center": "中心对称",
        "top": "上对称",
        "bottom": "下对称"
    }

    symmetry_name = symmetry_names.get(symmetry_type, "对称")

    # 检查是否为回复消息
    if not hasattr(event, 'reply'):
        await image_symmetry_handler.finish(f"请回复一条图片消息来使用{symmetry_name}功能")

    reply = event.reply
    if not reply:
        await image_symmetry_handler.finish(f"请回复一条图片消息来使用{symmetry_name}功能")

    # 获取图片消息
    image_found = False
    image_url = None

    for segment in reply.message:
        if segment.type == "image":
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

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:

                if isinstance(event, GroupMessageEvent):
                    PLUGIN_ID = "image_processor:symmetry"  # 确保ID一致
                    group_id = str(event.group_id)
                    user_id = str(event.user_id)
                    update_cd(PLUGIN_ID, group_id, user_id)

                # 直接使用send方法发送图片，避免重复发送
                await image_symmetry_handler.send(MessageSegment.image(f"file:///{result_path}"))
                # 不调用finish，让函数自然结束
            else:
                await image_symmetry_handler.send(f"图片{symmetry_name}处理失败，生成的文件异常")
        else:
            await image_symmetry_handler.send(f"图片{symmetry_name}处理失败")
    except FinishedException:
        raise
    except asyncio.TimeoutError:
        await image_symmetry_handler.send(f"图片{symmetry_name}处理超时，请稍后重试")
    except Exception as e:
        await image_symmetry_handler.send(f"处理图片时出错: {str(e)}")


@image_help_handler.handle()
async def handle_image_help(event: Event, cmd_arg: Message = CommandArg()):
    """处理图片处理帮助"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await image_help_handler.finish("图片处理在本群未开启！")
            return

    try:
        await image_help_handler.send("正在生成帮助信息...")

        # 尝试生成帮助图片
        help_image_path = await generate_help_image()

        if help_image_path and os.path.exists(help_image_path):
            file_size = os.path.getsize(help_image_path)
            if file_size > 100:
                await image_help_handler.send(MessageSegment.image(f"file:///{help_image_path}"))
            else:
                # 如果图片生成失败，回退到文本帮助
                help_text = await get_help_text()
                await image_help_handler.send(help_text)
        else:
            # 如果图片生成失败，回退到文本帮助
            help_text = await get_help_text()
            await image_help_handler.send(help_text)

    except Exception as e:
        # 如果出现任何错误，回退到文本帮助
        help_text = await get_help_text()
        await image_help_handler.send(f"生成帮助图片时出错，以下是文本帮助：\n{help_text}")


@video_to_gif_handler.handle()
async def handle_video_to_gif(event: Event, bot: Bot, cmd_arg: Message = CommandArg()):  # 移除默认值，使用依赖注入
    """处理视频转GIF"""
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id), user_id):
            await video_to_gif_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "video_to_gif", str(event.group_id), user_id):
            await video_to_gif_handler.finish("视频转GIF功能在本群无法使用！")
            return

    # 检查是否为回复消息
    if not hasattr(event, 'reply'):
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
        logger.warning(f"警告: 获取 Access Token 失败: {e} (如果OneBot实现未配置Token则此项可选)")

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
            logger.info(f"找到视频消息段: url={url[:100] if url else 'None'}, file={file_name}")

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
                f"找到文件消息段: file={file_name}, url={url[:100] if url else 'None'}, file_id={_file_id}, file_size={file_size}")

            # 检查是否为视频文件
            if file_name and any(file_name.lower().endswith(ext) for ext in
                                 ['.mp4', '.avi', '.mov', '.webm', '.mkv', '.flv', '.wmv']):
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
        logger.info(f"检测到群文件 file_id: {file_id}，尝试调用 OneBot API 获取下载链接...")
        try:
            api_response = await bot.call_api("get_group_file_url", group_id=event.group_id, file_id=file_id)
            new_url = api_response.get("url")
            if not new_url:
                raise Exception("API call 'get_group_file_url' did not return a URL.")

            video_url = new_url
            video_found = True
            logger.info(f"成功获取 Go-CQHTTP 代理 URL: {video_url[:200]}...")
        except Exception as e:
            logger.error(f"调用 get_group_file_url 失败: {e}")
            await video_to_gif_handler.finish(f"获取群文件下载链接失败: {e}。请确保Bot具有群文件权限。")

    elif video_found and not video_url:
        if video_file_name:
            await video_to_gif_handler.finish(
                f"检测到视频文件 '{video_file_name}'，但无法获取下载链接。请确保视频文件可通过URL直接访问。")
        else:
            await video_to_gif_handler.finish("检测到视频但无法获取下载信息，请联系管理员检查配置。")

    if not video_found or not video_url:
        error_msg = "回复的消息中没有找到视频，请确保回复的是视频消息。"
        error_msg += "\n支持格式: MP4, AVI, MOV, WebM, MKV, FLV, WMV"
        await video_to_gif_handler.finish(error_msg)

    try:
        await video_to_gif_handler.send("正在处理视频转GIF，这可能需要一些时间，请稍候...")

        result_path = await convert_video_to_gif(video_url, video_file_name, expected_file_size, access_token)

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                await video_to_gif_handler.finish(MessageSegment.image(f"file:///{result_path}"))
            else:
                await video_to_gif_handler.finish("生成的GIF文件异常，处理失败")
        else:
            await video_to_gif_handler.finish("视频转GIF处理失败")

    except FinishedException:
        raise
    except Exception as e:
        await video_to_gif_handler.finish(f"处理视频时出错: {str(e)}")


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
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else "private"

    # 插件开关与权限检查
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", group_id, user_id):
            await image_mirror_handler.finish("图片处理在本群未开启！")
            return

        # 使用 mirror 作为功能标识符
        if not is_feature_enabled("image_processor", "mirror", group_id, user_id):
            await image_mirror_handler.finish("镜像功能在本群无法使用！")
            return

        PLUGIN_ID = "image_processor:mirror"
        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_mirror_handler.finish(f"镜像功能还在冷却中，请等待 {remaining_cd} 秒")
            return

    # 定义中文名称
    direction_names = {
        "horizontal": "水平镜像",
        "vertical": "垂直镜像"
    }
    action_name = direction_names.get(direction, "镜像")

    # 检查回复
    if not hasattr(event, 'reply') or not event.reply:
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

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                if isinstance(event, GroupMessageEvent):
                    PLUGIN_ID = "image_processor:mirror"
                    update_cd(PLUGIN_ID, group_id, user_id)

                await image_mirror_handler.send(MessageSegment.image(f"file:///{result_path}"))
            else:
                await image_mirror_handler.send(f"图片{action_name}处理失败，文件异常")
        else:
            await image_mirror_handler.send(f"图片{action_name}处理失败")

    except FinishedException:
        raise
    except Exception as e:
        await image_mirror_handler.send(f"处理图片时出错: {str(e)}")


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
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else "private"

    # 1. 插件与功能开关检查
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", group_id, user_id):
            await image_rotate_handler.finish("图片处理在本群未开启！")
            return

        # 使用 rotate 作为功能标识符
        if not is_feature_enabled("image_processor", "rotate", group_id, user_id):
            await image_rotate_handler.finish("旋转功能在本群无法使用！")
            return

        # CD 检查
        PLUGIN_ID = "image_processor:rotate"
        remaining_cd = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining_cd > 0:
            await image_rotate_handler.finish(f"旋转功能还在冷却中，请等待 {remaining_cd} 秒")
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
    if not hasattr(event, 'reply') or not event.reply:
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
        await image_rotate_handler.send(f"正在生成{action_name}动画 (倍速: {speed})，请稍候...")

        result_path = await process_image_rotate(image_url, direction, speed)

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
                # 更新 CD
                if isinstance(event, GroupMessageEvent):
                    PLUGIN_ID = "image_processor:rotate"
                    update_cd(PLUGIN_ID, group_id, user_id)

                await image_rotate_handler.send(MessageSegment.image(f"file:///{result_path}"))
            else:
                await image_rotate_handler.send(f"{action_name}失败，生成文件异常")
        else:
            await image_rotate_handler.send(f"{action_name}处理失败")

    except FinishedException:
        raise
    except Exception as e:
        await image_rotate_handler.send(f"处理出错: {str(e)}")