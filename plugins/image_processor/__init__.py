from nonebot import on_command, on_message
from nonebot.rule import to_me
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent, PrivateMessageEvent
from nonebot.exception import FinishedException
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

from ..plugin_manager import *


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
        print(f"GIF检测错误: {e}")
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

@gif_reverse_handler.handle()
async def handle_gif_reverse(event: Event, cmd_arg: Message = CommandArg()):
    """处理GIF倒放"""
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id)):
            await gif_reverse_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "reverse", str(event.group_id)):
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
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id)):
            await image_cutout_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "cutout", str(event.group_id)):
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
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id)):
            await gif_speed_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "speed", str(event.group_id)):
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
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id)):
            await image_symmetry_handler.finish("图片处理在本群未开启！")
            return

    if isinstance(event, GroupMessageEvent):
        if not is_feature_enabled("image_processor", "symmetry", str(event.group_id)):
            await image_symmetry_handler.finish("对称功能在本群无法使用！")
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
            print(f"发送处理中消息失败: {send_error}")
            # 继续处理，不因为发送失败而中断

        # 直接异步调用对称处理函数
        result_path = await process_image_symmetry(image_url, symmetry_type)

        if result_path and os.path.exists(result_path):
            file_size = os.path.getsize(result_path)
            if file_size > 100:
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
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("image_processor", str(event.group_id)):
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