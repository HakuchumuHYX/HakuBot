# __init__.py
import asyncio
import re
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger

from ..utils.common import *
from ..plugin_manager import *

from .send import load_sticker_list, get_random_sticker, get_random_stickers, resolve_folder_name
from .contribution import extract_contribution_info, save_contribution_images
from .statistics import handle_statistics_command, get_sticker_statistics, render_stickers_preview
from .manage import handle_manage_command

# 初始化时加载配置
load_sticker_list()

# 创建消息处理器
sticker_matcher = on_message(priority=10, block=False)


def parse_multi_random_command(message_text: str) -> tuple[str, int] | None:
    """
    解析多图随机命令

    返回: (文件夹名, 图片数量) 或 None
    """
    # 匹配格式：随机文件夹名x数量
    match = re.match(r'^随机(\S+?)x(\d+)$', message_text.strip())
    if match:
        folder_name = match.group(1).strip()
        try:
            count = int(match.group(2))
            # 限制数量在1-5之间
            count = max(1, min(count, 5))
            return folder_name, count
        except ValueError:
            pass
    return None


@sticker_matcher.handle()
async def handle_sticker(event: GroupMessageEvent):
    # 只处理群聊消息
    if not isinstance(event, GroupMessageEvent):
        return

    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("stickers", str(event.group_id)):
            return

    # 获取纯文本消息
    message_text = event.get_plaintext().strip()
    if not message_text:
        return

    # 检查是否是管理命令
    manage_reply = await handle_manage_command(message_text, event)
    if manage_reply is not None:
        await sticker_matcher.finish(manage_reply)

    # 检查是否是查看统计命令
    if handle_statistics_command(message_text):
        # 渲染贴图预览图片
        try:
            pic_bytes = await render_stickers_preview()

            if pic_bytes:
                # 发送图片
                await sticker_matcher.send(MessageSegment.image(pic_bytes))
                return
        except Exception as e:
            logger.error(f"生成或发送贴图预览图片失败: {e}")

        # 如果图片生成或发送失败，使用文本统计
        statistics_info = get_sticker_statistics()
        await sticker_matcher.finish(statistics_info)

    # 检查是否是投稿格式
    folder_name, is_contribution = extract_contribution_info(message_text)
    if is_contribution:
        # 处理投稿
        success, reply_msg, saved_count = await save_contribution_images(folder_name, event)
        if success or saved_count == 0:  # 成功或完全失败时回复
            await sticker_matcher.finish(reply_msg)
        return

    # 检查是否是单图随机命令
    if message_text.startswith("随机"):
        # 先检查是否为多图随机命令
        multi_random_result = parse_multi_random_command(message_text)
        if multi_random_result:
            folder_name, count = multi_random_result
            sticker_files = get_random_stickers(folder_name, count)
            if sticker_files:
                # 发送多张图片
                try:
                    # 创建包含多张图片的消息
                    message_segments = []
                    for sticker_file in sticker_files:
                        message_segments.append(MessageSegment.image(sticker_file))

                    await sticker_matcher.finish(Message(message_segments))
                except Exception:
                    # 如果发送失败，静默处理，不发送错误信息
                    pass
            return

        # 如果不是多图随机命令，处理单图随机命令
        # 提取文件夹名（去掉"随机"前缀）
        folder_name = message_text[2:].strip()
        if folder_name:
            # 使用支持别名的函数获取贴图
            sticker_file = get_random_sticker(folder_name)
            if sticker_file:
                # 发送图片
                try:
                    await sticker_matcher.finish(MessageSegment.image(sticker_file))
                except Exception:
                    # 如果发送失败，静默处理，不发送错误信息
                    pass