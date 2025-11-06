# poke_reply/utils/common.py
import hashlib
import aiohttp
import base64
from typing import Tuple, List
from nonebot import logger
import re
from nonebot.adapters.onebot.v11 import (
    PokeNotifyEvent, Message, MessageEvent, GroupMessageEvent, MessageSegment, Bot
)
from nonebot.exception import FinishedException
from pathlib import Path
from nonebot.rule import Rule


async def download_image(image_url: str) -> Tuple[bool, bytes, str]:
    """下载图片并返回数据和文件扩展名"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    content_type = response.headers.get('Content-Type', '')
                    if 'jpeg' in content_type or 'jpg' in content_type:
                        extension = 'jpg'
                    elif 'png' in content_type:
                        extension = 'png'
                    elif 'gif' in content_type:
                        extension = 'gif'
                    else:
                        extension = 'jpg'
                    return True, image_data, extension
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
    return False, b'', ''


async def download_and_hash_image(image_url: str) -> Tuple[bool, str]:
    """下载图片并计算MD5哈希值"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    image_hash = hashlib.md5(image_data).hexdigest()
                    return True, image_hash
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
    return False, ""


def get_group_id(event) -> int:
    """从事件中获取群号"""
    if hasattr(event, 'group_id'):
        return event.group_id
    return 0


def extract_image_data(message: Message) -> Tuple[bool, list]:
    """提取消息中的图片数据"""
    images = []
    for segment in message:
        if segment.type == "image":
            image_url = segment.data.get("url", "")
            images.append(("image", image_url, segment))
        elif segment.type == "face":
            face_id = segment.data.get("id", "")
            images.append(("face", face_id, segment))
    return len(images) > 0, images


def preprocess_text(text: str) -> str:
    """预处理文本：去除标点符号和空格，转换为小写"""
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower()


def ensure_at_me():
    """确保消息at了机器人"""

    async def _checker(event: GroupMessageEvent) -> bool:
        for segment in event.original_message:
            if segment.type == "at" and segment.data.get("qq") == str(event.self_id):
                return True
        return False

    return Rule(_checker)


async def create_forward_message(bot: Bot, group_id: int, messages: List[Tuple[str, str, str]]) -> List[dict]:
    """
    创建合并转发消息（支持文本和图片base64）
    messages: 列表，元素为 (发送者名称, "text"或"image", 内容或base64)
    """
    try:
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "机器人")
        bot_uin = bot_info.get("user_id", bot.self_id)
        forward_nodes = []
        for sender_name, msg_type, content in messages:
            if msg_type == "text":
                node_content = MessageSegment.text(content)
            elif msg_type == "image":
                # content 应为 "base64://..." 格式
                node_content = MessageSegment.image(content)
            else:
                node_content = MessageSegment.text(str(content))

            node = {
                "type": "node",
                "data": {
                    "name": sender_name,
                    "uin": str(bot_uin),
                    "content": node_content
                }
            }
            forward_nodes.append(node)
        return forward_nodes
    except Exception as e:
        logger.error(f"创建合并转发消息失败: {e}")
        return [
            {
                "type": "node",
                "data": {
                    "name": "错误",
                    "uin": str(bot.self_id),
                    "content": "合并转发消息创建失败"
                }
            }
        ]


def image_to_base64(image_path: Path) -> Tuple[bool, str]:
    """将本地图片转换为base64编码"""
    try:
        if not image_path.exists():
            return False, f"图片文件不存在: {image_path}"
        if image_path.stat().st_size > 10 * 1024 * 1024:  # 10MB限制
            return False, f"图片文件过大: {image_path.stat().st_size / 1024 / 1024:.2f}MB"

        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')

        return True, f"base64://{base64_data}"
    except Exception as e:
        logger.error(f"图片转base64失败: {e}")
        return False, f"图片转换失败: {str(e)}"