# stickers/contribution.py
"""
Stickers 插件 - 投稿功能模块
"""

import re
import asyncio
import aiofiles
import tempfile
from pathlib import Path
from typing import List, Tuple, Set, Optional
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot
from nonebot import logger
from utils.images.formats import MIME_TYPES
from utils.logging import get_exc_desc
from utils.network.images import fetch_image
from utils.onebot.media import image_segment

from plugins.stickers.send import (
    sticker_folders,
    resolve_folder_name,
    count_images_in_folder,
    get_next_image_id,
    invalidate_count_cache,
)
from plugins.stickers.check import check_duplicate_images
from plugins.stickers.render import render_duplicate_report
from plugins.stickers.config import DOWNLOAD_CONCURRENCY


def extract_contribution_info(message_text: str) -> Tuple[str, bool, bool]:
    """
    提取投稿信息（支持别名和强制上传）

    Returns:
        (文件夹名, 是否为投稿格式, 是否强制上传)
    """
    match_force = re.match(r"^(.+?)投稿\s+force$", message_text.strip(), re.IGNORECASE)
    if match_force:
        return match_force.group(1).strip(), True, True

    match_normal = re.match(r"^(.+?)投稿$", message_text.strip())
    if match_normal:
        return match_normal.group(1).strip(), True, False

    return "", False, False


async def download_single_image(image_url: str) -> Optional[Path]:
    """下载单张图片到临时文件，扩展名按图片内容判断"""
    try:
        image = await fetch_image(
            image_url, timeout=30, allowed_formats=tuple(MIME_TYPES)
        )
    except Exception as e:
        logger.warning(f"下载图片失败: {image_url}, 错误: {get_exc_desc(e)}")
        return None

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{image.extension}")
    temp_path = Path(temp_file.name)
    temp_file.close()

    async with aiofiles.open(temp_path, "wb") as f:
        await f.write(image.data)
    return temp_path


async def download_images_parallel(image_urls: List[str]) -> List[Path]:
    """并行下载多张图片"""
    if not image_urls:
        return []

    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

    async def download_with_semaphore(url: str):
        async with semaphore:
            return await download_single_image(url)

    results = await asyncio.gather(
        *(download_with_semaphore(url) for url in image_urls)
    )
    temp_files = [path for path in results if path is not None]

    logger.debug(f"并行下载完成: {len(temp_files)}/{len(image_urls)} 张图片成功")
    return temp_files


async def save_contribution_images(
    bot: Bot, folder_name: str, event: GroupMessageEvent, force: bool = False
) -> Tuple[bool, str, int]:
    """保存投稿图片到指定文件夹"""
    temp_files: List[Path] = []

    try:
        actual_folder_name = resolve_folder_name(folder_name)

        if actual_folder_name not in sticker_folders:
            return False, "投稿失败！请使用「查看stickers」查看目前可投稿的文件夹！", 0

        folder_path = sticker_folders[actual_folder_name]

        # --- 提取图片 ---
        image_segments: List[MessageSegment] = []

        # Case 1: 回复合并转发消息
        if event.reply:
            forward_id = None
            for segment in event.reply.message:
                if segment.type == "forward":
                    forward_id = segment.data.get("id")
                    break

            if forward_id:
                try:
                    forward_data = await bot.get_forward_msg(id=forward_id)
                    nodes = forward_data.get("messages", [])

                    for node in nodes:
                        try:
                            content = node.get("message", "")
                            segments_list = []

                            if isinstance(content, str):
                                if content:
                                    segments_list.append(
                                        {"type": "text", "data": {"text": content}}
                                    )
                            elif isinstance(content, dict):
                                segments_list.append(content)
                            elif isinstance(content, list):
                                for item in content:
                                    if isinstance(item, str):
                                        if item:
                                            segments_list.append(
                                                {"type": "text", "data": {"text": item}}
                                            )
                                    elif isinstance(item, dict):
                                        segments_list.append(item)

                            for segment_dict in segments_list:
                                if segment_dict.get("type") == "image":
                                    try:
                                        image_seg = MessageSegment(
                                            type=segment_dict["type"],
                                            data=segment_dict.get("data", {}),
                                        )
                                        image_segments.append(image_seg)
                                    except Exception as e:
                                        logger.warning(f"无法解析图片 segment: {e}")
                        except Exception as e:
                            logger.opt(exception=True).warning(f"解析合并转发节点失败: {e}")

                except Exception as e:
                    logger.warning(f"获取合并转发消息失败: {get_exc_desc(e)}")
                    return False, "投稿失败！解析合并转发内容失败，请稍后再试。", 0

        # Case 2: 回复普通消息
        if not image_segments and event.reply:
            for segment in event.reply.message:
                if segment.type == "image":
                    image_segments.append(segment)

        # Case 3: 投稿命令本身包含图片
        if not image_segments:
            for segment in event.message:
                if segment.type == "image":
                    image_segments.append(segment)

        if not image_segments:
            return (
                False,
                "投稿失败！未检测到图片，请直接发送图片、回复图片或回复合并转发消息",
                0,
            )

        # --- 并行下载图片 ---
        image_urls = list(
            dict.fromkeys(
                segment.data["url"]
                for segment in image_segments
                if segment.data.get("url")
            )
        )

        temp_files = await download_images_parallel(image_urls)

        if not temp_files:
            return False, "投稿失败！无法下载任何图片", 0

        # --- 查重与保存 ---
        duplicates: List[Tuple[Path, Path]] = []
        duplicate_temp_files: Set[Path] = set()

        if not force:
            has_duplicates, duplicates = await check_duplicate_images(
                folder_name, temp_files
            )
            if has_duplicates:
                duplicate_temp_files = {dup[1] for dup in duplicates}

        saved_count = 0
        saved_files: List[Path] = []
        duplicate_count = len(duplicate_temp_files)

        for temp_file in temp_files:
            if temp_file not in duplicate_temp_files:
                try:
                    next_id = get_next_image_id()
                    filename = f"{next_id}{temp_file.suffix}"
                    file_path = folder_path / filename

                    async with aiofiles.open(file_path, "wb") as f:
                        async with aiofiles.open(temp_file, "rb") as temp_f:
                            content = await temp_f.read()
                            await f.write(content)

                    saved_count += 1
                    saved_files.append(file_path)
                except Exception as e:
                    logger.exception(f"保存图片时出错: {e}")

        # 保存成功后使缓存失效
        if saved_count > 0:
            invalidate_count_cache(actual_folder_name)

        # --- 报告结果 ---
        total_processed = saved_count + duplicate_count
        logger.info(
            f"群 {event.group_id} 向 {actual_folder_name} 投稿："
            f"保存 {saved_count} 张，重复 {duplicate_count} 张"
        )

        if total_processed == 0:
            return False, "投稿失败！无法处理任何图片。", 0

        image_count = count_images_in_folder(actual_folder_name)
        display_name = actual_folder_name
        if actual_folder_name != folder_name:
            display_name = f"{actual_folder_name}(通过别名'{folder_name}')"

        report_bytes = None
        if duplicate_count > 0:
            report_bytes = await render_duplicate_report(folder_name, duplicates)

        if saved_count == 0 and duplicate_count > 0:
            if report_bytes:
                return False, image_segment(report_bytes), 0
            else:
                return (
                    False,
                    f"投稿失败！检测到 {duplicate_count} 张图片全部为重复图片。",
                    0,
                )

        message_segments = [
            MessageSegment.text(f"投稿完成！成功保存 {saved_count} 张图片。")
        ]

        if duplicate_count > 0:
            message_segments.append(
                MessageSegment.text(f"\n检测到 {duplicate_count} 张重复图片。")
            )
            if report_bytes:
                message_segments.append(image_segment(report_bytes))
            else:
                message_segments.append(MessageSegment.text("\n（重复报告生成失败）"))

        message_segments.append(
            MessageSegment.text(f"\n现在 {display_name} 中共有 {image_count} 张表情~")
        )

        return True, Message(message_segments), saved_count

    finally:
        logger.debug(f"清理 {len(temp_files)} 个临时文件...")
        for temp_file in temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception as e:
                logger.warning(f"清理临时文件失败 {temp_file}: {e}")
