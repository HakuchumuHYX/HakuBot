# plugins/buaa_msm/handlers/upload.py
"""
文件上传入口（私聊）：
- buaa上传文件：进入等待状态
- 收到 file 消息：保存文件 -> ingest_bin（预解密、记录来访角色等）-> 自动 run_msr()
- 取消：退出等待状态

说明：
- 迁移自 plugins/buaa_msm/data_upload.py
- 这里仅做 NoneBot 事件适配；入库调用 services.ingest_service，MSR 生成调用 services.msr_service.run_msr
"""

from __future__ import annotations
from utils.paths import PluginPaths

import shutil
import time
from pathlib import Path
from typing import Dict
from urllib.parse import unquote

import aiofiles
import aiohttp
from nonebot import on_command, on_message, require, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type

from plugins.buaa_msm.config import plugin_config
from plugins.buaa_msm.exceptions import DataLoadError
from plugins.buaa_msm.services.ingest_service import ingest_bin, reserve_storage_path
from plugins.buaa_msm.services.msr_service import run_msr
from plugins.buaa_msm.services.processing_guard import is_processing, set_processing

# 等待文件上传的用户字典：{user_id: entered_at_epoch_seconds}
waiting_for_file: Dict[str, float] = {}


def _cleanup_expired_waiting(now: float | None = None) -> None:
    """清理过期的上传等待态"""
    current = now if now is not None else time.time()
    ttl = max(1, int(plugin_config.upload_wait_timeout_seconds))
    expired_users = [
        uid
        for uid, entered_at in waiting_for_file.items()
        if current - entered_at > ttl
    ]
    for uid in expired_users:
        waiting_for_file.pop(uid, None)


# ============== 上传命令 ==============

# 同一命令只注册一个 matcher，私聊/群聊由按事件类型分流的 handler 处理（避免 Duplicated prefix rule 警告）
upload_cmd = on_command("buaa上传文件", priority=5, block=True)


@upload_cmd.handle()
async def handle_upload_command(bot: Bot, event: PrivateMessageEvent):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)
    waiting_for_file[user_id] = time.time()
    await upload_cmd.finish(
        "已准备好接收文件，请直接发送您要上传的文件。如需取消，请发送'取消'。"
    )


# 群聊提示（挂在同一 matcher 上，群聊事件走此 handler）
@upload_cmd.handle()
async def handle_group_upload(bot: Bot, event: GroupMessageEvent):
    await upload_cmd.finish("该指令仅在私聊中生效")


# ============== 文件接收处理 ==============

file_handler = on_message(priority=10, block=False)


@file_handler.handle()
async def handle_file_message(
    bot: Bot, event: PrivateMessageEvent | GroupMessageEvent
):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)

    if user_id not in waiting_for_file:
        return

    # 检查是否有文件
    file_segments = [seg for seg in event.message if seg.type == "file"]

    if not file_segments:
        # 等待态的提示只回私聊，避免在群里刷「未检测到文件」
        if isinstance(event, GroupMessageEvent):
            return
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

        logger.debug(f"收到文件: {file_name}, ID: {file_id}, 大小: {file_size} 字节")

        # get_file 只解析图片/语音缓存；普通文件按会话类型取下载链接
        if isinstance(event, GroupMessageEvent):
            file_url_result = await bot.call_api(
                "get_group_file_url",
                group_id=event.group_id,
                file_id=file_id,
            )
        else:
            file_url_result = await bot.call_api(
                "get_private_file_url",
                user_id=event.user_id,
                file_id=file_id,
                file_hash=str(file_data.get("file_hash") or ""),
            )
        file_url = (
            file_url_result.get("url", "")
            if isinstance(file_url_result, dict)
            else file_url_result
        )

        if not file_url:
            await file_handler.send("无法获取文件路径，请重试。")
            return

        # 处理文件名
        if not file_name or file_name == "unknown":
            file_name = f"file_{user_id}_{int(event.time)}.bin"

        # 命名前置：先按绑定名生成目标路径（已去重），再执行保存
        saved_file_path = reserve_storage_path(file_name, user_id)
        await save_file(file_url, saved_file_path, user_id)

        # 移除等待状态
        waiting_for_file.pop(user_id, None)

        await file_handler.send(
            f"文件上传成功！\n文件名：{saved_file_path.name}\n正在进行预解密，请稍候..."
        )

        decrypted_data = await ingest_bin(user_id, saved_file_path)

        if decrypted_data:
            try:
                import nonebot_plugin_localstore as store

                display_path = saved_file_path.relative_to(PluginPaths("buaa_msm").data)
            except (ValueError, Exception):
                display_path = saved_file_path

            await file_handler.send(
                f"文件预解密成功！\n保存位置：{display_path}\n正在自动生成分析结果..."
            )

            # 自动执行 MSR 分析
            try:
                if await is_processing(user_id):
                    await file_handler.send(
                        "您有其他请求正在处理中，跳过自动分析。您可以稍后手动使用 'buaamsr' 命令。"
                    )
                else:
                    await set_processing(user_id, True)
                    try:
                        await run_msr(
                            bot=bot,
                            user_id=user_id,
                            event_user_id=event.user_id,
                            send_func=file_handler.send,
                        )
                    finally:
                        await set_processing(user_id, False)
            except FinishedException:
                raise
            except DataLoadError as e:
                logger.info(f"自动执行 MSR 分析时无法读取用户数据: {e}")
                await file_handler.send(
                    f"自动分析失败: {str(e)}\n您可以稍后手动使用 'buaamsr' 重新生成分析结果。"
                )
            except Exception as e:
                logger.exception("自动执行 MSR 分析失败")
                await file_handler.send(
                    f"自动分析失败: {str(e)}\n您可以稍后手动使用 'buaamsr' 重新生成分析结果。"
                )
        else:
            await file_handler.send(
                "文件上传成功，但预解密失败！\n请检查文件是否为正确的 .bin 文件，然后重新上传。"
            )

    except FinishedException:
        raise
    except Exception as e:
        logger.exception("文件处理失败")
        waiting_for_file.pop(user_id, None)
        await file_handler.send(f"文件上传失败：{str(e)}")


# ============== 取消命令 ==============

cancel_cmd = on_command(
    "取消", rule=is_type(PrivateMessageEvent), priority=5, block=True
)


@cancel_cmd.handle()
async def handle_cancel_command(bot: Bot, event: PrivateMessageEvent):
    _cleanup_expired_waiting()
    user_id = str(event.user_id)
    if user_id in waiting_for_file:
        waiting_for_file.pop(user_id, None)
        await cancel_cmd.finish("已取消文件上传。")
    # 如果不在等待状态，不做任何响应


# ============== 文件保存工具函数 ==============


async def save_file(file_path: str, destination_path: Path, user_id: str):
    """
    把 QQ 文件（URL 或本地路径）保存到 destination_path（由 reserve_storage_path 生成）
    """
    if file_path.startswith(("http://", "https://")):
        await download_file(file_path, destination_path)
    else:
        await copy_local_file(file_path, destination_path)

    logger.info(f"用户 {user_id} 上传文件: {destination_path.name}，保存至 {destination_path}")


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

    if decoded_path.startswith("/app/.config/QQ"):
        decoded_path = decoded_path.replace("/app/.config/QQ", "/opt/SnowLuma/qq-config/QQ", 1)

    source_file = Path(decoded_path)

    if not source_file.exists():
        raise Exception(f"源文件不存在: {decoded_path}")

    shutil.copy2(source_file, destination_path)
