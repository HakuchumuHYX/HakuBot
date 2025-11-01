import nonebot
from nonebot import on_command, on_message, require
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, GroupMessageEvent
from nonebot.log import logger
import aiohttp
import aiofiles
import os
import shutil
from pathlib import Path
from typing import Set
from urllib.parse import unquote, urlparse

# 声明依赖并导入 nonebot-plugin-localstore
require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# 导入重命名模块
from .data_rename import rename_uploaded_file
from .data_manage import remove_old_user_files, update_user_latest_file

# 使用 localstore 获取插件数据目录
data_dir: Path = store.get_plugin_data_dir()
# 在数据目录下创建 msmdata 文件夹
file_storage_dir = data_dir / "msmdata"
file_storage_dir.mkdir(parents=True, exist_ok=True)

logger.success(f"文件上传插件数据目录已设置为: {file_storage_dir}")

# 存储等待文件上传的用户状态
waiting_for_file: Set[str] = set()

# 上传文件命令
upload_cmd = on_command("buaa上传文件", priority=5, block=True)


@upload_cmd.handle()
async def handle_upload_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    # 设置用户状态为等待文件
    waiting_for_file.add(user_id)

    await upload_cmd.finish("已准备好接收文件，请直接发送您要上传的文件。如需取消，请发送'取消'。")


# 处理群聊中的上传文件命令
@upload_cmd.handle()
async def handle_group_upload_command(bot: Bot, event: GroupMessageEvent):
    await upload_cmd.finish("该指令仅在私聊中生效")


# 处理文件消息
file_handler = on_message(priority=10, block=False)


@file_handler.handle()
async def handle_file_message(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    # 如果用户不在等待文件状态，不处理
    if user_id not in waiting_for_file:
        return

    # 检查消息中是否包含文件
    file_segments = []
    for segment in event.message:
        if segment.type == "file":
            file_segments.append(segment)

    if not file_segments:
        await file_handler.send("未检测到文件，请发送文件或输入'取消'退出上传模式。")
        return

    # 处理第一个文件
    file_segment = file_segments[0]

    try:
        # 获取文件信息
        file_data = file_segment.data
        file_name = file_data.get("file", "")
        file_id = file_data.get("file_id", "")
        file_size = file_data.get("file_size", 0)

        logger.info(f"收到文件: {file_name}, ID: {file_id}, 大小: {file_size} 字节")

        # 获取文件路径/URL
        file_url_result = await bot.get_file(file_id=file_id)

        # 处理不同类型的返回值
        if isinstance(file_url_result, dict):
            # 如果是字典，尝试从中提取URL
            file_url = file_url_result.get("url", "")
        else:
            # 如果是字符串，直接使用
            file_url = file_url_result

        if not file_url:
            await file_handler.send("无法获取文件路径，请重试。")
            return

        logger.info(f"获取到文件路径: {file_url}")

        # 处理文件名
        if not file_name or file_name == "unknown":
            # 使用默认文件名
            file_name = f"file_{user_id}_{int(event.time)}"

        # 确保文件名安全
        safe_filename = "".join(c for c in file_name if c.isalnum() or c in "._- ")
        if not safe_filename:
            safe_filename = f"file_{user_id}_{int(event.time)}.bin"

        # 下载并保存文件
        saved_file_path, unique_filename = await save_file(file_url, safe_filename, user_id)

        # 重命名文件
        new_filename = rename_uploaded_file(unique_filename, user_id)

        # 更新显示路径
        if new_filename != unique_filename:
            saved_file_path = file_storage_dir / new_filename
            try:
                display_path = saved_file_path.relative_to(data_dir)
            except ValueError:
                display_path = saved_file_path
        else:
            try:
                display_path = saved_file_path.relative_to(data_dir)
            except ValueError:
                display_path = saved_file_path

        # 移除等待状态
        waiting_for_file.discard(user_id)
        
        # 在 data_upload.py 的 handle_file_message 函数中，文件保存成功后添加：

        # 更新用户最新文件记录
        update_user_latest_file(user_id, saved_file_path)

        # 删除用户的其他旧文件
        remove_old_user_files(user_id, saved_file_path)

        # 使用 send() 而不是 finish() 来避免 FinishedException
        await file_handler.send(
            f"文件上传成功！\n文件名：{new_filename}\n文件大小：{file_size} 字节\n保存位置：{display_path}")

    except Exception as e:
        logger.error(f"文件处理失败: {e}")
        waiting_for_file.discard(user_id)
        # 同样使用 send() 而不是 finish() 来发送错误消息
        await file_handler.send(f"文件上传失败：{str(e)}")


# 取消上传命令
cancel_cmd = on_command("取消", priority=5, block=True)


@cancel_cmd.handle()
async def handle_cancel_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    if user_id in waiting_for_file:
        waiting_for_file.discard(user_id)
        await cancel_cmd.finish("已取消文件上传。")
    else:
        await cancel_cmd.finish("您当前没有等待上传的文件。")


# 处理群聊中的取消命令
@cancel_cmd.handle()
async def handle_group_cancel_command(bot: Bot, event: GroupMessageEvent):
    await cancel_cmd.finish("该指令仅在私聊中生效")


async def save_file(file_path: str, filename: str, user_id: str):
    """
    保存文件到 localstore 管理的数据目录
    支持本地文件路径和HTTP URL

    Returns:
        tuple: (文件路径, 实际文件名)
    """
    # 确保目标目录存在
    file_storage_dir.mkdir(parents=True, exist_ok=True)

    # 处理文件名冲突
    base_name, ext = os.path.splitext(filename)
    counter = 1
    unique_filename = filename
    destination_path = file_storage_dir / unique_filename

    while destination_path.exists():
        unique_filename = f"{base_name}_{counter}{ext}"
        destination_path = file_storage_dir / unique_filename
        counter += 1

    # 判断是本地文件还是HTTP URL
    if file_path.startswith(('http://', 'https://')):
        # HTTP URL - 下载文件
        await download_file(file_path, destination_path)
    else:
        # 本地文件路径 - 直接复制
        await copy_local_file(file_path, destination_path)

    logger.info(f"用户 {user_id} 上传文件: {unique_filename}，保存至 {destination_path}")
    return destination_path, unique_filename


async def download_file(url: str, destination_path: Path):
    """通过HTTP下载文件"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"下载失败，HTTP状态码: {response.status}")

            # 保存文件
            async with aiofiles.open(str(destination_path), 'wb') as f:
                async for chunk in response.content.iter_chunked(1024):
                    await f.write(chunk)


async def copy_local_file(source_path: str, destination_path: Path):
    """复制本地文件"""
    # 解码URL编码的路径
    decoded_path = unquote(source_path)

    # 检查源文件是否存在
    source_file = Path(decoded_path)
    if not source_file.exists():
        raise Exception(f"源文件不存在: {decoded_path}")

    # 复制文件
    shutil.copy2(source_file, destination_path)


# 查看已上传文件列表命令 (仅超级用户可使用)
from nonebot.permission import SUPERUSER

list_files_cmd = on_command("文件列表", permission=SUPERUSER, priority=5, block=True)


@list_files_cmd.handle()
async def handle_list_files_command(bot: Bot, event: PrivateMessageEvent):
    """列出已上传的所有文件"""
    try:
        # 确保路径是字符串类型
        files = list(Path(file_storage_dir).iterdir())
        if not files:
            await list_files_cmd.finish("文件存储目录为空。")
            return

        file_list = []
        for i, file in enumerate(files):
            file_size = file.stat().st_size
            # 格式化文件大小
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.2f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.2f} MB"

            file_list.append(f"{i + 1}. {file.name} ({size_str})")

        message = f"已上传的文件 (共 {len(files)} 个):\n" + "\n".join(file_list)

        # 如果消息过长，可能需分条发送，此处简化处理
        await list_files_cmd.finish(message)
    except Exception as e:
        logger.error(f"列出文件失败: {e}")
        await list_files_cmd.finish("列出文件失败。")


# 处理群聊中的文件列表命令
@list_files_cmd.handle()
async def handle_group_list_files_command(bot: Bot, event: GroupMessageEvent):
    await list_files_cmd.finish("该指令仅在私聊中生效")


# 插件加载成功提示
logger.success("增强版文件上传插件加载成功！")