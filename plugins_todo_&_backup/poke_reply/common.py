# utils/common.py
import hashlib
import aiohttp
from typing import Tuple
from nonebot import logger
import re
from typing import Tuple, List
from nonebot.adapters.onebot.v11 import Message

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