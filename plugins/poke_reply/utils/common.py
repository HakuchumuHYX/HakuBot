# utils/common.py
import hashlib
import aiohttp
import base64

from typing import Tuple
from nonebot import logger
import re
from typing import Tuple, List
from nonebot.adapters.onebot.v11 import Message
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import PokeNotifyEvent, Message, MessageEvent, GroupMessageEvent, MessageSegment, Bot
from nonebot.exception import FinishedException
from pathlib import Path

async def download_image(image_url: str) -> Tuple[bool, bytes, str]:
    """下载图片并返回数据和文件扩展名"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    # 根据Content-Type确定文件扩展名
                    content_type = response.headers.get('Content-Type', '')
                    if 'jpeg' in content_type or 'jpg' in content_type:
                        extension = 'jpg'
                    elif 'png' in content_type:
                        extension = 'png'
                    elif 'gif' in content_type:
                        extension = 'gif'
                    else:
                        # 默认使用jpg
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
    return 0  # 私聊返回0

def extract_image_data(message: Message) -> Tuple[bool, list]:
    """提取消息中的图片数据"""
    images = []
    for segment in message:
        if segment.type == "image":
            # 获取图片URL
            image_url = segment.data.get("url", "")
            images.append(("image", image_url, segment))
        elif segment.type == "face":
            # 表情符号
            face_id = segment.data.get("id", "")
            images.append(("face", face_id, segment))

    return len(images) > 0, images

def preprocess_text(text: str) -> str:
    """预处理文本：去除标点符号和空格，转换为小写"""
    # 移除标点符号和表情符号
    text = re.sub(r'[^\w\s]', '', text)
    # 转换为小写
    return text.lower()

def ensure_at_me():
    """确保消息at了机器人"""
    async def _checker(event: GroupMessageEvent) -> bool:
        for segment in event.original_message:
            if segment.type == "at" and segment.data.get("qq") == str(event.self_id):
                return True
        return False
    return _checker

async def create_forward_message(bot: Bot, group_id: int, messages: List[Tuple[str, str, str]]) -> List[dict]:
    """
    创建合并转发消息（支持文本和图片）

    Args:
        bot: 机器人实例
        group_id: 群组ID
        messages: 消息列表，每个元素为 (发送者名称, 消息类型, 消息内容)
                  消息类型: "text" 或 "image"
                  消息内容: 对于文本是文本内容，对于图片是base64编码

    Returns:
        合并转发消息节点列表
    """
    try:
        # 获取机器人信息
        bot_info = await bot.get_login_info()
        bot_name = bot_info.get("nickname", "机器人")
        bot_uin = bot_info.get("user_id", bot.self_id)

        forward_nodes = []

        for sender_name, msg_type, content in messages:
            # 创建转发消息节点
            if msg_type == "text":
                node = {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": str(bot_uin),
                        "content": content
                    }
                }
            elif msg_type == "image":
                # 创建包含图片的节点
                node = {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": str(bot_uin),
                        "content": [
                            {
                                "type": "image",
                                "data": {
                                    "file": content  # base64编码的图片数据
                                }
                            }
                        ]
                    }
                }
            else:
                # 未知类型，默认为文本
                node = {
                    "type": "node",
                    "data": {
                        "name": sender_name,
                        "uin": str(bot_uin),
                        "content": content
                    }
                }

            forward_nodes.append(node)

        return forward_nodes

    except Exception as e:
        logger.error(f"创建合并转发消息失败: {e}")
        # 如果合并转发失败，返回普通文本消息格式
        return [
            {
                "type": "node",
                "data": {
                    "name": "投稿内容",
                    "uin": str(bot.self_id),
                    "content": "合并转发消息创建失败，请稍后重试喵！"
                }
            }
        ]

def image_to_base64(image_path: Path) -> Tuple[bool, str]:
    """
    将本地图片转换为base64编码

    Args:
        image_path: 图片文件路径

    Returns:
        Tuple[bool, str]: (是否成功, base64编码字符串或错误信息)
    """
    try:
        if not image_path.exists():
            return False, f"图片文件不存在: {image_path}"

        # 检查文件大小，避免过大
        file_size = image_path.stat().st_size
        if file_size > 10 * 1024 * 1024:  # 10MB限制
            return False, f"图片文件过大: {file_size / 1024 / 1024:.2f}MB"

        # 读取图片并编码为base64
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')

            # 根据文件扩展名确定MIME类型
            extension = image_path.suffix.lower()
            if extension == '.jpg' or extension == '.jpeg':
                mime_type = 'jpeg'
            elif extension == '.png':
                mime_type = 'png'
            elif extension == '.gif':
                mime_type = 'gif'
            else:
                mime_type = 'jpeg'  # 默认

            base64_string = f"base64://{base64_data}"
            return True, base64_string

    except Exception as e:
        logger.error(f"图片转base64失败: {e}")
        return False, f"图片转换失败: {str(e)}"