import random
from pathlib import Path
from typing import Dict, List

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot_plugin_localstore import get_data_dir

from ..utils.common import *
from ..plugin_manager import *

# 插件数据
sticker_dir = get_data_dir("stickers")
sticker_dir.mkdir(parents=True, exist_ok=True)

# 存储所有贴图文件夹的映射
sticker_folders: Dict[str, Path] = {}


def scan_sticker_folders():
    """扫描所有贴图文件夹"""
    global sticker_folders
    sticker_folders.clear()

    if sticker_dir.exists():
        for folder in sticker_dir.iterdir():
            if folder.is_dir():
                sticker_folders[folder.name] = folder


def get_random_sticker(folder_name: str) -> Path | None:
    """从指定文件夹中随机获取一张贴图"""
    if folder_name not in sticker_folders:
        return None

    folder = sticker_folders[folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # 收集所有图片文件
    image_files = []
    for ext in image_extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    if not image_files:
        return None

    return random.choice(image_files)


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

    # 检查是否是贴图文件夹名称
    sticker_file = get_random_sticker(message_text)
    if sticker_file:
        # 发送图片
        try:
            await sticker_matcher.finish(MessageSegment.image(sticker_file))
        except Exception:
            # 如果发送失败，静默处理，不发送错误信息
            pass