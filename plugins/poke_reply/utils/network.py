import hashlib
import aiohttp
from typing import Tuple
from nonebot import logger

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
