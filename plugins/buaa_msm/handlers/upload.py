# plugins/buaa_msm/handlers/upload.py
"""
文件上传入口（私聊）：
- buaa上传文件：进入等待状态
- 收到 file 消息：保存文件 -> 预解密 -> 记录来访角色 -> 自动 run_msr()
- 取消：退出等待状态

说明：
- 迁移自 plugins/buaa_msm/data_upload.py
- 这里仅做 NoneBot 事件适配；MSR 生成调用 services.msr_service.run_msr
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import unquote

import aiofiles
import aiohttp
from nonebot import on_command, on_message, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store  # noqa: E402

from .. import decrypt
from ..config import plugin_config
from ..data_rename import generate_target_filename
from ..infra.cache import cache_manager
from ..infra.storage import remove_old_user_files, update_user_latest_file
from ..infra.visit_history import record_character_visit
from ..services.msr_service import run_msr
from ..services.processing_guard import is_processing, set_processing

# 从配置获取路径
file_storage_dir = plugin_config.file_storage_dir

# 等待文件上传的用户字典：{user_id: entered_at_epoch_seconds}
waiting_for_file: Dict[str, float] = {}


def _cleanup_expired_waiting(now: float | None = None) -> None:
    """清理过期的上传等待态"""
    current = now if now is not None else time.time()
    ttl = max(1, int(plugin_config.upload_wait_timeout_seconds))
    expired_users = [uid for uid, entered_at in waiting_for_file.items() if current - entered_at > ttl]
    for uid in expired_users:
        waiting_for_file.pop(uid, None)


# ============== 上传命令 ==============

upload_cmd = on_command("buaa上传文件", rule=is_type(PrivateMessageEvent), priority=5, block=True)


@upload_cmd.handle()
async def handle_upload_command(bot: Bot, event: PrivateMessageEvent):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)
    waiting_for_file[user_id] = time.time()
    await upload_cmd.finish("已准备好接收文件，请直接发送您要上传的文件。如需取消，请发送'取消'。")


# 群聊提示
group_upload_cmd = on_command("buaa上传文件", rule=is_type(GroupMessageEvent), priority=5, block=True)


@group_upload_cmd.handle()
async def handle_group_upload(bot: Bot, event: GroupMessageEvent):
    await group_upload_cmd.finish("该指令仅在私聊中生效")


# ============== 文件接收处理 ==============

file_handler = on_message(priority=10, block=False)


@file_handler.handle()
async def handle_file_message(bot: Bot, event: PrivateMessageEvent):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)

    if user_id not in waiting_for_file:
        return

    # 检查是否有文件
    file_segments = [seg for seg in event.message if seg.type == "file"]

    if not file_segments:
        # 检查是否是取消命令
        msg_text = event.message.extract_plain_text().strip()
        if msg_text == "取消":
            waiting_for_file.pop(user_id, None)
            await file_handler.send("已取消文件上传。")
            return
        await file_handler.send("未检测到文件，请发送文件或输入'取消'退出上传模式。")
        return

    file_segment = file_segments[0]

    try:
        file_data = file_segment.data
        file_name = file_data.get("file", "")
        file_id = file_data.get("file_id", "")
        file_size = file_data.get("file_size", 0)

        logger.info(f"收到文件: {file_name}, ID: {file_id}, 大小: {file_size} 字节")

        # 获取文件URL
        file_url_result = await bot.get_file(file_id=file_id)
        file_url = file_url_result.get("url", "") if isinstance(file_url_result, dict) else file_url_result

        if not file_url:
            await file_handler.send("无法获取文件路径，请重试。")
            return

        # 处理文件名
        if not file_name or file_name == "unknown":
            file_name = f"file_{user_id}_{int(event.time)}.bin"

        # 命名前置：先生成最终目标文件名，再执行保存
        target_filename = generate_target_filename(file_name, user_id)

        # 保存文件（内部执行冲突去重）
        saved_file_path, unique_filename = await save_file(file_url, target_filename, user_id)

        # 移除等待状态
        waiting_for_file.pop(user_id, None)

        # 更新用户文件记录
        update_user_latest_file(user_id, saved_file_path)
        remove_old_user_files(user_id, saved_file_path)

        # 使缓存失效
        await cache_manager.invalidate(user_id)

        await file_handler.send(f"文件上传成功！\n文件名：{unique_filename}\n正在进行预解密，请稍候...")

        # 预解密
        user_output_dir = file_storage_dir / f"output_{user_id}"
        user_output_dir.mkdir(parents=True, exist_ok=True)
        json_output_file = user_output_dir / f"{saved_file_path.stem}_decrypted.json"

        decrypted_data = decrypt.decrypt_and_save(bin_file_path=saved_file_path, json_output_path=json_output_file)

        if decrypted_data:
            # 记录来访角色
            _record_visiting_characters(user_id, decrypted_data)

            try:
                display_path = saved_file_path.relative_to(store.get_plugin_data_dir())
            except ValueError:
                display_path = saved_file_path

            await file_handler.send(f"文件预解密成功！\n保存位置：{display_path}\n正在自动生成分析结果...")

            # 自动执行 MSR 分析
            try:
                if await is_processing(user_id):
                    await file_handler.send("您有其他请求正在处理中，跳过自动分析。您可以稍后手动使用 'buaamsr' 命令。")
                else:
                    await set_processing(user_id, True)
                    try:
                        await run_msr(bot=bot, user_id=user_id, event_user_id=event.user_id, send_func=file_handler.send)
                    finally:
                        await set_processing(user_id, False)
            except FinishedException:
                raise
            except Exception as e:
                logger.error(f"自动执行 MSR 分析失败: {e}")
                await file_handler.send(f"自动分析失败: {str(e)}\n您可以稍后手动使用 'buaamsr' 重新生成分析结果。")
        else:
            await file_handler.send("文件上传成功，但预解密失败！\n请检查文件是否为正确的 .bin 文件，然后重新上传。")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"文件处理失败: {e}")
        waiting_for_file.pop(user_id, None)
        await file_handler.send(f"文件上传失败：{str(e)}")


def _record_visiting_characters(user_id: str, decrypted_data: dict):
    """记录来访角色"""
    try:
        char_visit_list = decrypted_data.get("userMysekaiGateCharacterVisit", {}).get("userMysekaiGateCharacters", [])
        char_ids = []
        for char in char_visit_list:
            gid = char.get("mysekaiGameCharacterUnitGroupId")
            if gid:
                char_ids.append(str(gid))

        if char_ids:
            record_character_visit(user_id, char_ids)
            logger.info(f"已记录用户 {user_id} 的 {len(char_ids)} 个来访角色ID")
    except Exception as e:
        logger.error(f"记录来访角色失败: {e}")


# ============== 取消命令 ==============

cancel_cmd = on_command("取消", rule=is_type(PrivateMessageEvent), priority=5, block=True)


@cancel_cmd.handle()
async def handle_cancel_command(bot: Bot, event: PrivateMessageEvent):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)
    if user_id in waiting_for_file:
        waiting_for_file.pop(user_id, None)
        await cancel_cmd.finish("已取消文件上传。")
    # 如果不在等待状态，不做任何响应


# ============== 文件保存工具函数 ==============

async def save_file(file_path: str, filename: str, user_id: str) -> Tuple[Path, str]:
    """
    保存文件到存储目录

    Args:
        file_path: 文件URL或本地路径
        filename: 文件名（建议为最终目标文件名）
        user_id: 用户ID

    Returns:
        (保存路径, 实际文件名)
    """
    file_storage_dir.mkdir(parents=True, exist_ok=True)

    # 生成唯一文件名（在目标文件名基础上做冲突去重）
    base_name, ext = os.path.splitext(filename)
    counter = 1
    unique_filename = filename
    destination_path = file_storage_dir / unique_filename

    while destination_path.exists():
        unique_filename = f"{base_name}_{counter}{ext}"
        destination_path = file_storage_dir / unique_filename
        counter += 1

    # 下载或复制文件
    if file_path.startswith(("http://", "https://")):
        await download_file(file_path, destination_path)
    else:
        await copy_local_file(file_path, destination_path)

    logger.info(f"用户 {user_id} 上传文件: {unique_filename}，保存至 {destination_path}")
    return destination_path, unique_filename


async def download_file(url: str, destination_path: Path):
    """从URL下载文件"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"下载失败，HTTP状态码: {response.status}")
            async with aiofiles.open(str(destination_path), "wb") as f:
                async for chunk in response.content.iter_chunked(1024):
                    await f.write(chunk)


async def copy_local_file(source_path: str, destination_path: Path):
    """复制本地文件"""
    decoded_path = unquote(source_path)

    # 将 NapCat 容器内路径映射到宿主机路径
    if decoded_path.startswith("/app/.config/QQ"):
        decoded_path = decoded_path.replace("/app/.config/QQ", "/opt/NapCat/qq_data", 1)

    source_file = Path(decoded_path)

    if not source_file.exists():
        raise Exception(f"源文件不存在: {decoded_path}")

    shutil.copy2(source_file, destination_path)


logger.success("文件上传 handlers 已加载成功！")
