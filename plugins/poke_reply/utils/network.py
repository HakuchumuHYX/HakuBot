import hashlib
import io
import aiohttp
from typing import Tuple
from nonebot import logger
from PIL import Image

IMAGE_DOWNLOAD_TIMEOUT = 15
MAX_IMAGE_DOWNLOAD_BYTES = 10 * 1024 * 1024
IMAGE_READ_CHUNK_SIZE = 64 * 1024

IMAGE_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def get_image_extension(content_type: str):
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return IMAGE_CONTENT_TYPES.get(normalized)


def validate_image_bytes(image_data: bytes, extension: str) -> bool:
    if not image_data:
        return False
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            image.verify()
        return True
    except Exception as e:
        logger.error(f"图片内容校验失败 ({extension}): {e}")
        return False


async def read_limited_response(response, max_bytes: int = MAX_IMAGE_DOWNLOAD_BYTES) -> Tuple[bool, bytes]:
    chunks = []
    total_size = 0
    async for chunk in response.content.iter_chunked(IMAGE_READ_CHUNK_SIZE):
        total_size += len(chunk)
        if total_size > max_bytes:
            logger.error(f"图片下载超过大小限制: {total_size} > {max_bytes}")
            return False, b""
        chunks.append(chunk)
    return True, b"".join(chunks)

async def download_image(image_url: str) -> Tuple[bool, bytes, str]:
    """下载图片并返回数据和文件扩展名"""
    try:
        timeout = aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    extension = get_image_extension(content_type)
                    if not extension:
                        logger.error(f"不支持的图片 Content-Type: {content_type}")
                        return False, b'', ''
                    success, image_data = await read_limited_response(response)
                    if not success:
                        return False, b'', ''
                    if not validate_image_bytes(image_data, extension):
                        return False, b'', ''
                    return True, image_data, extension
                logger.error(f"下载图片失败，HTTP状态码: {response.status}")
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
    return False, b'', ''

async def download_and_hash_image(image_url: str) -> Tuple[bool, str]:
    """下载图片并计算MD5哈希值"""
    try:
        timeout = aiohttp.ClientTimeout(total=IMAGE_DOWNLOAD_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(image_url) as response:
                if response.status == 200:
                    content_type = response.headers.get('Content-Type', '')
                    extension = get_image_extension(content_type)
                    if not extension:
                        logger.error(f"不支持的图片 Content-Type: {content_type}")
                        return False, ""
                    success, image_data = await read_limited_response(response)
                    if not success:
                        return False, ""
                    if not validate_image_bytes(image_data, extension):
                        return False, ""
                    image_hash = hashlib.md5(image_data).hexdigest()
                    return True, image_hash
                logger.error(f"下载图片失败，HTTP状态码: {response.status}")
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
    return False, ""
