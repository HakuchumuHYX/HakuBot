import nonebot
from nonebot import require, get_driver
from nonebot.log import logger
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import re

# 声明依赖并导入 nonebot-plugin-localstore
require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# 导入定时任务插件
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 使用 localstore 获取插件数据目录
data_dir: Path = store.get_plugin_data_dir()
# 文件存储目录
file_storage_dir = data_dir / "msmdata"

# 存储每个QQ号的最新文件路径
user_latest_files: Dict[str, Path] = {}


def load_user_latest_files():
    """加载已存在的文件，初始化用户最新文件字典"""
    global user_latest_files
    user_latest_files = {}

    try:
        if not file_storage_dir.exists():
            return

        # 遍历所有文件，提取QQ号信息
        for file_path in file_storage_dir.iterdir():
            if file_path.is_file():
                # 尝试从文件名中提取QQ号
                user_id = extract_user_id_from_filename(file_path.name)
                if user_id:
                    # 更新用户最新文件
                    update_user_latest_file(user_id, file_path)

        logger.info(f"已加载 {len(user_latest_files)} 个用户的文件记录")
    except Exception as e:
        logger.error(f"加载用户文件记录失败: {e}")


def extract_user_id_from_filename(filename: str) -> str:
    """从文件名中提取QQ号"""
    # 尝试多种模式匹配QQ号
    patterns = [
        r'^(\d+)_',  # 格式: QQ号_绑定内容_时间
        r'^(\d+)\.',  # 格式: QQ号.扩展名
        r'_(\d+)_',  # 格式: 其他_QQ号_其他
    ]

    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            return match.group(1)

    return ""


def update_user_latest_file(user_id: str, file_path: Path):
    """更新用户的最新文件记录"""
    if user_id not in user_latest_files:
        user_latest_files[user_id] = file_path
    else:
        # 比较文件修改时间，保留最新的
        current_mtime = user_latest_files[user_id].stat().st_mtime
        new_mtime = file_path.stat().st_mtime

        if new_mtime > current_mtime:
            # 删除旧文件
            try:
                user_latest_files[user_id].unlink()
                logger.info(f"删除用户 {user_id} 的旧文件: {user_latest_files[user_id].name}")
            except Exception as e:
                logger.error(f"删除旧文件失败: {e}")

            # 更新为新文件
            user_latest_files[user_id] = file_path


def cleanup_all_files():
    """清理msmdata目录中的所有文件"""
    try:
        if not file_storage_dir.exists():
            logger.info("文件存储目录不存在，无需清理")
            return

        # 获取所有文件
        files = list(file_storage_dir.iterdir())
        if not files:
            logger.info("文件存储目录为空，无需清理")
            return

        # 删除所有文件
        deleted_count = 0
        for file_path in files:
            if file_path.is_file():
                try:
                    file_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"删除文件失败 {file_path}: {e}")

        # 清空用户文件记录
        user_latest_files.clear()

        logger.success(f"定时清理完成，删除了 {deleted_count} 个文件")
    except Exception as e:
        logger.error(f"定时清理失败: {e}")


def remove_old_user_files(user_id: str, keep_file: Path):
    """删除用户除指定文件外的所有其他文件"""
    try:
        if not file_storage_dir.exists():
            return

        deleted_count = 0
        for file_path in file_storage_dir.iterdir():
            if file_path.is_file() and file_path != keep_file:
                # 检查是否为该用户的文件
                file_user_id = extract_user_id_from_filename(file_path.name)
                if file_user_id == user_id:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                        logger.info(f"删除用户 {user_id} 的旧文件: {file_path.name}")
                    except Exception as e:
                        logger.error(f"删除用户旧文件失败 {file_path}: {e}")

        if deleted_count > 0:
            logger.info(f"为用户 {user_id} 删除了 {deleted_count} 个旧文件")

    except Exception as e:
        logger.error(f"删除用户旧文件失败: {e}")


# 注册定时任务 - 每天5:00和17:00清理文件
@scheduler.scheduled_job("cron", hour=5, minute=0, id="cleanup_files_5am")
async def cleanup_files_5am():
    logger.info("执行早上5:00文件清理任务")
    cleanup_all_files()


@scheduler.scheduled_job("cron", hour=17, minute=0, id="cleanup_files_5pm")
async def cleanup_files_5pm():
    logger.info("执行下午5:00文件清理任务")
    cleanup_all_files()


# 手动清理文件命令
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent

cleanup_cmd = on_command("清理文件", permission=SUPERUSER, priority=5, block=True)


@cleanup_cmd.handle()
async def handle_cleanup_command(bot: Bot, event: PrivateMessageEvent):
    """手动清理所有文件"""
    try:
        cleanup_all_files()
        await cleanup_cmd.finish("文件清理完成")
    except Exception as e:
        logger.error(f"手动清理文件失败: {e}")
        await cleanup_cmd.finish(f"文件清理失败: {str(e)}")


# 查看文件统计命令
stats_cmd = on_command("文件统计", permission=SUPERUSER, priority=5, block=True)


@stats_cmd.handle()
async def handle_stats_command(bot: Bot, event: PrivateMessageEvent):
    """查看文件统计信息"""
    try:
        if not file_storage_dir.exists():
            await stats_cmd.finish("文件存储目录不存在")
            return

        files = list(file_storage_dir.iterdir())
        if not files:
            await stats_cmd.finish("文件存储目录为空")
            return

        # 统计信息
        total_files = len(files)
        total_size = sum(f.stat().st_size for f in files)

        # 按用户分组
        user_files = {}
        for file_path in files:
            user_id = extract_user_id_from_filename(file_path.name) or "未知用户"
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append(file_path)

        # 格式化统计信息
        size_str = format_file_size(total_size)
        user_stats = "\n".join([f"  - {user_id}: {len(files)} 个文件"
                                for user_id, files in user_files.items()])

        message = f"文件统计信息:\n总文件数: {total_files}\n总大小: {size_str}\n按用户分布:\n{user_stats}"

        await stats_cmd.finish(message)

    except Exception as e:
        logger.error(f"获取文件统计失败: {e}")
        await stats_cmd.finish(f"获取文件统计失败: {str(e)}")


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


# 初始化加载用户文件记录
load_user_latest_files()

# 插件加载成功提示
logger.success("文件管理模块加载成功！")