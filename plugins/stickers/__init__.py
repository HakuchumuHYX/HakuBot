# __init__.py
import asyncio
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.log import logger

from ..utils.common import *
from ..plugin_manager import *

from .send import load_sticker_list, get_random_sticker, resolve_folder_name
from .contribution import extract_contribution_info, save_contribution_images
from .statistics import handle_statistics_command, get_sticker_statistics, render_stickers_preview
from .manage import handle_manage_command  # 新增导入

# 初始化时加载配置
load_sticker_list()

# 创建消息处理器
sticker_matcher = on_message(priority=10, block=False)


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

    # 检查是否是随机贴图命令
    if message_text.startswith("随机"):
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