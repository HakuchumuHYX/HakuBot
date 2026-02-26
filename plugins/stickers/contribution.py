# stickers/contribution.py
"""
Stickers 插件 - 投稿功能模块
"""
import re
import asyncio
import aiohttp
import aiofiles
import tempfile
from pathlib import Path
from typing import List, Tuple, Set, Dict, Optional
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot
from nonebot.log import logger

from .send import sticker_dir, sticker_folders, resolve_folder_name, count_images_in_folder, get_next_image_id, invalidate_count_cache
from .check import check_duplicate_images, render_duplicate_report
from .config import DOWNLOAD_CONCURRENCY


def extract_contribution_info(message_text: str) -> Tuple[str, bool, bool]:
    """
    提取投稿信息（支持别名和强制上传）
    
    Returns:
        (文件夹名, 是否为投稿格式, 是否强制上传)
    """
    match_force = re.match(r'^(.+?)投稿\s+force$', message_text.strip(), re.IGNORECASE)
    if match_force:
        return match_force.group(1).strip(), True, True

    match_normal = re.match(r'^(.+?)投稿$', message_text.strip())
    if match_normal:
        return match_normal.group(1).strip(), True, False

    return "", False, False


def determine_image_extension(image_segment: MessageSegment, response: aiohttp.ClientResponse = None) -> str:
    """根据图片消息段确定文件扩展名"""
    if response:
        content_type = response.headers.get('Content-Type', '')
        if content_type:
            type_map = {
                'image/jpeg': '.jpg', 'image/jpg': '.jpg',
                'image/png': '.png', 'image/gif': '.gif',
                'image/bmp': '.bmp', 'image/webp': '.webp'
            }
            for mime_type, ext in type_map.items():
                if mime_type in content_type:
                    return ext

    url = image_segment.data.get("url", "")
    if url:
        for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
            if ext in url.lower():
                return ext if ext != '.jpeg' else '.jpg'

    return ".jpg"


async def download_single_image(
    session: aiohttp.ClientSession,
    image_url: str,
    segment: MessageSegment
) -> Optional[Path]:
    """下载单张图片到临时文件"""
    try:
        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                image_data = await response.read()
                file_extension = determine_image_extension(segment, response)
                
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
                temp_path = Path(temp_file.name)
                temp_file.close()
                
                async with aiofiles.open(temp_path, "wb") as f:
                    await f.write(image_data)
                
                return temp_path
            else:
                logger.error(f"下载图片失败: {image_url}, 状态码: {response.status}")
    except asyncio.TimeoutError:
        logger.error(f"下载图片超时: {image_url}")
    except Exception as e:
        logger.error(f"下载图片异常: {image_url}, 错误: {e}")
    return None


async def download_images_parallel(image_urls: Dict[str, MessageSegment]) -> List[Path]:
    """并行下载多张图片"""
    if not image_urls:
        return []
    
    temp_files: List[Path] = []
    semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    
    async def download_with_semaphore(session: aiohttp.ClientSession, url: str, segment: MessageSegment):
        async with semaphore:
            return await download_single_image(session, url, segment)
    
    connector = aiohttp.TCPConnector(limit=DOWNLOAD_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [download_with_semaphore(session, url, seg) for url, seg in image_urls.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Path):
                temp_files.append(result)
            elif isinstance(result, Exception):
                logger.warning(f"下载任务异常: {result}")
    
    logger.info(f"并行下载完成: {len(temp_files)}/{len(image_urls)} 张图片成功")
    return temp_files


async def save_contribution_images(
    bot: Bot,
    folder_name: str,
    event: GroupMessageEvent,
    force: bool = False
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
                                    segments_list.append({'type': 'text', 'data': {'text': content}})
                            elif isinstance(content, dict):
                                segments_list.append(content)
                            elif isinstance(content, list):
                                for item in content:
                                    if isinstance(item, str):
                                        if item:
                                            segments_list.append({'type': 'text', 'data': {'text': item}})
                                    elif isinstance(item, dict):
                                        segments_list.append(item)

                            for segment_dict in segments_list:
                                if segment_dict.get('type') == 'image':
                                    try:
                                        image_seg = MessageSegment(
                                            type=segment_dict['type'],
                                            data=segment_dict.get('data', {})
                                        )
                                        image_segments.append(image_seg)
                                    except Exception as e:
                                        logger.warning(f"无法解析图片 segment: {e}")
                        except Exception as e:
                            logger.error(f"解析合并转发节点失败: {e}")

                except Exception as e:
                    logger.error(f"获取合并转发消息失败: {e}")
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
            return False, "投稿失败！未检测到图片，请直接发送图片、回复图片或回复合并转发消息", 0

        # --- 并行下载图片 ---
        image_urls: Dict[str, MessageSegment] = {}
        for segment in image_segments:
            image_url = segment.data.get("url")
            if image_url:
                image_urls[image_url] = segment

        temp_files = await download_images_parallel(image_urls)

        if not temp_files:
            return False, "投稿失败！无法下载任何图片", 0

        # --- 查重与保存 ---
        duplicates: List[Tuple[Path, Path]] = []
        duplicate_temp_files: Set[Path] = set()

        if not force:
            has_duplicates, duplicates = await check_duplicate_images(folder_name, temp_files)
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
                    logger.error(f"保存图片时出错: {e}")

        # 保存成功后使缓存失效
        if saved_count > 0:
            invalidate_count_cache(actual_folder_name)

        # --- 报告结果 ---
        total_processed = saved_count + duplicate_count

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
                return False, MessageSegment.image(report_bytes), 0
            else:
                return False, f"投稿失败！检测到 {duplicate_count} 张图片全部为重复图片。", 0

        message_segments = [MessageSegment.text(f"投稿完成！成功保存 {saved_count} 张图片。")]

        if duplicate_count > 0:
            message_segments.append(MessageSegment.text(f"\n检测到 {duplicate_count} 张重复图片。"))
            if report_bytes:
                message_segments.append(MessageSegment.image(report_bytes))
            else:
                message_segments.append(MessageSegment.text("\n（重复报告生成失败）"))

        message_segments.append(MessageSegment.text(f"\n现在 {display_name} 中共有 {image_count} 张表情~"))

        return True, Message(message_segments), saved_count

    finally:
        logger.info(f"清理 {len(temp_files)} 个临时文件...")
        for temp_file in temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception as e:
                logger.error(f"清理临时文件失败 {temp_file}: {e}")
