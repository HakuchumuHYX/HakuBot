# contribution.py
import re
import aiohttp
import aiofiles
from pathlib import Path
from typing import List, Tuple, Optional
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent
from nonebot import get_bot

from .send import sticker_dir, sticker_folders, scan_sticker_folders, count_images_in_folder


def extract_contribution_info(message_text: str) -> Tuple[str, bool]:
    """
    提取投稿信息
    返回: (文件夹名, 是否为投稿格式)
    """
    # 匹配格式：文件夹名投稿
    match = re.match(r'^(.+?)投稿$', message_text.strip())
    if match:
        folder_name = match.group(1).strip()
        return folder_name, True
    return "", False


async def save_contribution_images(folder_name: str, event: GroupMessageEvent) -> Tuple[bool, str, int]:
    """
    保存投稿图片到指定文件夹

    返回: (是否成功, 消息, 保存的图片数量)
    """
    # 检查文件夹是否存在
    if folder_name not in sticker_folders:
        available_folders = list(sticker_folders.keys())
        if available_folders:
            folder_list = ", ".join(available_folders)
            return False, f"投稿失败！当前可用文件夹：{folder_list}", 0
        else:
            return False, "投稿失败！暂无可用贴图文件夹", 0

    folder_path = sticker_folders[folder_name]

    # 提取消息中的所有图片
    image_segments = []

    # 情况1: 直接发送图片 + 文字
    for segment in event.message:
        if segment.type == "image":
            image_segments.append(segment)

    # 情况2: 回复图片消息
    if not image_segments and event.reply:
        # 从回复的消息中提取图片
        for segment in event.reply.message:
            if segment.type == "image":
                image_segments.append(segment)

    if not image_segments:
        return False, "投稿失败！未检测到图片，请直接发送图片或回复图片消息", 0

    saved_count = 0

    for i, segment in enumerate(image_segments):
        try:
            # 获取图片URL
            image_url = segment.data.get("url")
            if not image_url:
                continue

            # 使用 aiohttp 下载图片
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()

                        # 确定文件扩展名
                        file_extension = determine_image_extension(segment, response)

                        # 生成唯一文件名
                        import time
                        import random
                        timestamp = int(time.time())
                        random_num = random.randint(1000, 9999)
                        filename = f"contribution_{timestamp}_{random_num}{file_extension}"

                        # 保存文件
                        file_path = folder_path / filename
                        async with aiofiles.open(file_path, "wb") as f:
                            await f.write(image_data)

                        saved_count += 1
                    else:
                        print(f"下载图片失败，状态码: {response.status}")
                        continue

        except Exception as e:
            # 记录错误但继续处理其他图片
            print(f"保存图片时出错: {e}")
            continue

    if saved_count > 0:
        # 重新扫描文件夹以更新文件列表
        scan_sticker_folders()

        # 计算当前文件夹中的图片数量
        image_count = count_images_in_folder(folder_name)

        if saved_count == 1:
            return True, f"投稿成功！现在 {folder_name} 中有 {image_count} 张表情~", saved_count
        else:
            return True, f"投稿成功！成功保存 {saved_count} 张图片到 {folder_name}，现在共有 {image_count} 张表情~", saved_count
    else:
        return False, "投稿失败！无法保存任何图片", 0


def determine_image_extension(image_segment: MessageSegment, response: aiohttp.ClientResponse = None) -> str:
    """
    根据图片消息段确定文件扩展名
    """
    # 优先从响应头获取 Content-Type
    if response:
        content_type = response.headers.get('Content-Type', '')
        if content_type:
            type_map = {
                'image/jpeg': '.jpg',
                'image/jpg': '.jpg',
                'image/png': '.png',
                'image/gif': '.gif',
                'image/bmp': '.bmp',
                'image/webp': '.webp'
            }
            for mime_type, ext in type_map.items():
                if mime_type in content_type:
                    return ext

    # 尝试从URL中提取扩展名
    url = image_segment.data.get("url", "")
    if url:
        # 常见的图片扩展名
        extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        for ext in extensions:
            if ext in url.lower():
                return ext

    # 检查消息段中的Content-Type
    content_type = image_segment.data.get("type")
    if content_type:
        type_map = {
            'image/jpeg': '.jpg',
            'image/jpg': '.jpg',
            'image/png': '.png',
            'image/gif': '.gif',
            'image/bmp': '.bmp',
            'image/webp': '.webp'
        }
        if content_type in type_map:
            return type_map[content_type]

    # 默认使用.jpg
    return ".jpg"