# contribution.py
import re
import aiohttp
import aiofiles
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent
from nonebot import get_bot

from .send import sticker_dir, sticker_folders, resolve_folder_name, count_images_in_folder
from .check import check_duplicate_images, render_duplicate_report


def extract_contribution_info(message_text: str) -> Tuple[str, bool, bool]:
    """
    提取投稿信息（支持别名和强制上传）
    返回: (文件夹名, 是否为投稿格式, 是否强制上传)
    """
    # 匹配格式：文件夹名投稿 图片 force
    match_force = re.match(r'^(.+?)投稿\s+force$', message_text.strip(), re.IGNORECASE)
    if match_force:
        folder_name = match_force.group(1).strip()
        return folder_name, True, True

    # 匹配格式：文件夹名投稿
    match_normal = re.match(r'^(.+?)投稿$', message_text.strip())
    if match_normal:
        folder_name = match_normal.group(1).strip()
        return folder_name, True, False

    return "", False, False


async def save_contribution_images(folder_name: str, event: GroupMessageEvent, force: bool = False) -> Tuple[
    bool, str, int]:
    """
    保存投稿图片到指定文件夹（支持别名和查重）

    返回: (是否成功, 消息, 保存的图片数量)
    """
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    # 检查文件夹是否存在
    if actual_folder_name not in sticker_folders:
        # 获取可用文件夹信息
        from .send import get_folder_display_info
        folder_info_list = get_folder_display_info()

        if folder_info_list:
            folder_list = []
            for folder_info in folder_info_list:
                name = folder_info["name"]
                aliases = folder_info.get("aliases", [])
                if aliases:
                    folder_list.append(f"{name}(别名: {', '.join(aliases)})")
                else:
                    folder_list.append(name)

            available_folders = ", ".join(folder_list)
            return False, f"投稿失败！当前可用文件夹：{available_folders}", 0
        else:
            return False, "投稿失败！暂无可用贴图文件夹", 0

    folder_path = sticker_folders[actual_folder_name]

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

    # 下载图片到临时文件
    temp_files = []
    for segment in image_segments:
        try:
            image_url = segment.data.get("url")
            if not image_url:
                continue

            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()

                        # 创建临时文件
                        file_extension = determine_image_extension(segment, response)
                        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                            temp_file.write(image_data)
                            temp_files.append(Path(temp_file.name))
                    else:
                        print(f"下载图片失败，状态码: {response.status}")
        except Exception as e:
            print(f"下载图片到临时文件失败: {e}")

    if not temp_files:
        return False, "投稿失败！无法下载任何图片", 0

    # 检查重复图片（如果不是强制上传）
    duplicates = []
    if not force:
        has_duplicates, duplicates = await check_duplicate_images(folder_name, temp_files)
        if has_duplicates:
            # 生成重复报告图片
            report_bytes = await render_duplicate_report(folder_name, duplicates)
            if report_bytes:
                return False, MessageSegment.image(report_bytes), 0
            else:
                duplicate_names = ", ".join([f"{dup[1].name}" for dup in duplicates])
                return False, f"投稿失败！检测到重复图片: {duplicate_names}", 0

    # 保存非重复图片
    saved_count = 0
    saved_files = []

    for temp_file in temp_files:
        # 如果是强制上传或者不是重复图片，则保存
        is_duplicate = any(temp_file == dup[1] for dup in duplicates)
        if force or not is_duplicate:
            try:
                # 生成唯一文件名
                import time
                import random
                timestamp = int(time.time())
                random_num = random.randint(1000, 9999)
                filename = f"contribution_{timestamp}_{random_num}{temp_file.suffix}"

                # 保存文件
                file_path = folder_path / filename
                async with aiofiles.open(file_path, "wb") as f:
                    async with aiofiles.open(temp_file, "rb") as temp_f:
                        content = await temp_f.read()
                        await f.write(content)

                saved_count += 1
                saved_files.append(file_path)
            except Exception as e:
                print(f"保存图片时出错: {e}")

    # 清理临时文件
    for temp_file in temp_files:
        try:
            temp_file.unlink()
        except:
            pass

    if saved_count > 0:
        # 计算当前文件夹中的图片数量
        image_count = count_images_in_folder(actual_folder_name)

        # 显示实际文件夹名
        display_name = actual_folder_name
        if actual_folder_name != folder_name:
            display_name = f"{actual_folder_name}(通过别名'{folder_name}')"

        duplicate_info = ""
        if duplicates and force:
            duplicate_count = len(duplicates)
            duplicate_info = f"（强制上传，跳过{duplicate_count}张重复图片）"

        if saved_count == 1:
            return True, f"投稿成功{duplicate_info}！现在 {display_name} 中有 {image_count} 张表情~", saved_count
        else:
            return True, f"投稿成功{duplicate_info}！成功保存 {saved_count} 张图片到 {display_name}，现在共有 {image_count} 张表情~", saved_count
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