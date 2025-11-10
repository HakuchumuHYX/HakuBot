# contribution.py
import re
import aiohttp
import aiofiles
import tempfile
import time
import random
from pathlib import Path
from typing import List, Tuple, Optional, Set, Dict
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, Bot
from nonebot import get_bot
from nonebot.log import logger
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


async def save_contribution_images(bot: Bot, folder_name: str, event: GroupMessageEvent, force: bool = False) -> Tuple[
    bool, str, int]:
    """
    保存投稿图片到指定文件夹（支持别名、查重、合并转发）

    返回: (是否成功, 消息, 保存的图片数量)
    """
    # 1. 将 temp_files 移到 try 外部, 以便 finally 可以访问
    temp_files: List[Path] = []

    # 2. 【修复】整个函数体都包含在 try 块中
    try:
        # 解析实际文件夹名称
        actual_folder_name = resolve_folder_name(folder_name)

        # 检查文件夹是否存在
        if actual_folder_name not in sticker_folders:
            return False, "投稿失败！请使用”查看stickers“查看目前可投稿的文件夹！", 0

        folder_path = sticker_folders[actual_folder_name]

        # --- 提取图片 ---
        image_segments: List[MessageSegment] = []

        # Case 1: 回复一个合并转发消息
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

                    # 遍历节点列表
                    for node in nodes:
                        content = None  # <-- 【改进】确保 content 始终被定义
                        try:
                            # 4. 消息内容在 "message" 键中
                            content = node.get("message", "")

                            # vvvvvv 【已保留：BUG绕过方案】 vvvvvv
                            segments_list = []

                            if isinstance(content, str):
                                if content: segments_list.append({'type': 'text', 'data': {'text': content}})
                            elif isinstance(content, dict):
                                segments_list.append(content)
                            elif isinstance(content, list):
                                for item in content:
                                    if isinstance(item, str):
                                        if item: segments_list.append({'type': 'text', 'data': {'text': item}})
                                    elif isinstance(item, dict):
                                        segments_list.append(item)

                            # 6. 【已保留】直接遍历 dict 列表, 绕过 Message() 构造函数的 bug
                            for segment_dict in segments_list:
                                if segment_dict.get('type') == 'image':
                                    try:
                                        image_seg = MessageSegment(type=segment_dict['type'],
                                                                   data=segment_dict.get('data', {}))
                                        image_segments.append(image_seg)
                                    except Exception as segment_error:
                                        logger.warning(
                                            f"无法解析单个图片 segment: {segment_error} | Segment: {segment_dict}")
                            # ^^^^^^ 【已保留：BUG绕过方案】 ^^^^^^

                        except Exception as e:
                            # 【改进】现在这个 except 只捕获单个节点的解析失败
                            logger.error(f"解析合并转发 *节点* 失败: {e} | 节点内容: {content}")
                            # 继续处理下一个节点，而不是终止整个投稿
                            pass

                except Exception as e:
                    # 这个 except 捕获 "get_forward_msg" API 本身的失败
                    logger.error(f"获取合并转发消息失败: {e} | ID: {forward_id}")
                    return False, "投稿失败！解析合并转发内容失败，请稍后再试。", 0

        # Case 2: 回复一个普通消息 (包含图片)
        if not image_segments and event.reply:
            for segment in event.reply.message:
                if segment.type == "image":
                    image_segments.append(segment)

        # Case 3: 投稿命令消息本身包含图片
        if not image_segments:
            for segment in event.message:
                if segment.type == "image":
                    image_segments.append(segment)

        if not image_segments:
            return False, "投稿失败！未检测到图片，请直接发送图片、回复图片或回复合并转发消息", 0

        # --- 下载图片到临时文件 ---
        image_urls: Dict[str, MessageSegment] = {}
        for segment in image_segments:
            image_url = segment.data.get("url")
            if image_url:
                image_urls[image_url] = segment

        for image_url, segment in image_urls.items():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as response:
                        if response.status == 200:
                            image_data = await response.read()
                            file_extension = determine_image_extension(segment, response)
                            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                                temp_file.write(image_data)
                                temp_files.append(Path(temp_file.name))
                        else:
                            print(f"下载图片失败: {image_url}, 状态码: {response.status}")
            except Exception as e:
                print(f"下载图片到临时文件失败: {e}")

        if not temp_files:
            return False, "投稿失败！无法下载任何图片", 0

        # --- 查重与保存 (部分成功逻辑) ---
        duplicates: List[Tuple[Path, Path]] = []
        duplicate_temp_files: Set[Path] = set()

        if not force:
            has_duplicates, duplicates = await check_duplicate_images(folder_name, temp_files)
            if has_duplicates:
                duplicate_temp_files = {dup[1] for dup in duplicates}

        saved_count = 0
        saved_files: List[Path] = []
        duplicate_count = len(duplicate_temp_files)

        # 2. 遍历并保存非重复图片
        for temp_file in temp_files:
            if temp_file not in duplicate_temp_files:
                try:
                    timestamp = int(time.time())
                    random_num = random.randint(1000, 9999)
                    filename = f"contribution_{timestamp}_{random_num}{temp_file.suffix}"

                    file_path = folder_path / filename
                    async with aiofiles.open(file_path, "wb") as f:
                        async with aiofiles.open(temp_file, "rb") as temp_f:
                            content = await temp_f.read()
                            await f.write(content)

                    saved_count += 1
                    saved_files.append(file_path)
                except Exception as e:
                    print(f"保存图片时出错: {e}")

        # --- 报告结果 ---
        total_processed = saved_count + duplicate_count

        if total_processed == 0:
            return False, "投稿失败！无法处理任何图片。", 0

        image_count = count_images_in_folder(actual_folder_name)
        display_name = actual_folder_name
        if actual_folder_name != folder_name:
            display_name = f"{actual_folder_name}(通过别名'{folder_name}')"

        # Case B: 全部图片都重复
        if saved_count == 0 and duplicate_count > 0:
            # 【修复】此时临时文件仍然存在，报告可以正确生成
            report_bytes = await render_duplicate_report(folder_name, duplicates)
            if report_bytes:
                return False, MessageSegment.image(report_bytes), 0
            else:
                return False, f"投稿失败！检测到 {duplicate_count} 张图片全部为重复图片。", 0

        # Case C: 至少保存成功1张
        response_parts = [f"成功保存 {saved_count} 张图片"]
        if duplicate_count > 0:
            response_parts.append(f"检测到 {duplicate_count} 张重复图片")

        return True, f"投稿完成！{'，'.join(response_parts)}。现在 {display_name} 中共有 {image_count} 张表情~", saved_count

    # 3. 【修复】finally 块与 try 对齐，确保始终执行
    finally:
        # 无论函数如何返回 (成功, 失败, 异常)，这里总会执行
        print(f"清理 {len(temp_files)} 个临时文件...")
        for temp_file in temp_files:
            try:
                temp_file.unlink()
            except Exception as e:
                print(f"清理临时文件失败 {temp_file}: {e}")


def determine_image_extension(image_segment: MessageSegment, response: aiohttp.ClientResponse = None) -> str:
    """
    根据图片消息段确定文件扩展名
    """
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

    url = image_segment.data.get("url", "")
    if url:
        extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        for ext in extensions:
            if url.lower().endswith(ext) or f"{ext}?" in url.lower() or f"{ext}&" in url.lower():
                return ext

    return ".jpg"