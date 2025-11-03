import asyncio
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from ..utils.common import *
from ..plugin_manager import *

from .send import scan_sticker_folders, get_random_sticker, start_folder_watcher, stop_folder_watcher
from .contribution import extract_contribution_info, save_contribution_images
from .statistics import handle_statistics_command, get_sticker_statistics

# 初始化时扫描文件夹
scan_sticker_folders()

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

    # 检查是否是查看统计命令
    if handle_statistics_command(message_text):
        statistics_info = get_sticker_statistics()
        await sticker_matcher.finish(statistics_info)
        return

    # 检查是否是投稿格式
    folder_name, is_contribution = extract_contribution_info(message_text)
    if is_contribution:
        # 处理投稿
        success, reply_msg, saved_count = await save_contribution_images(folder_name, event.message)
        if success or saved_count == 0:  # 成功或完全失败时回复
            await sticker_matcher.finish(reply_msg)
        return

    # 检查是否是贴图文件夹名称
    sticker_file = get_random_sticker(message_text)
    if sticker_file:
        # 发送图片
        try:
            await sticker_matcher.finish(MessageSegment.image(sticker_file))
        except Exception:
            # 如果发送失败，静默处理，不发送错误信息
            pass


# 启动文件夹监视器
async def start_watcher():
    await start_folder_watcher()


# 停止文件夹监视器
async def stop_watcher():
    await stop_folder_watcher()


# 在插件加载和卸载时启动/停止监视器
from nonebot import get_driver

driver = get_driver()

@driver.on_startup
async def on_startup():
    await start_watcher()

@driver.on_shutdown
async def on_shutdown():
    await stop_watcher()