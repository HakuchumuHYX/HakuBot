# plugins/buaa_msm/services/maintenance_service.py
"""
维护/运维相关服务（services）

职责：
- 文件清理（清理存储目录中的所有文件/目录）
- 缓存清理
- 访问历史清理
- 文件统计/列表（供管理员命令调用）

说明：
- 不在这里注册 NoneBot 命令或 scheduler job（这些应放到 handlers 层）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from nonebot.log import logger

from ..infra.cache import cache_manager
from ..infra.storage import clear_user_latest_files, file_storage_dir
from ..infra.visit_history import visit_history_manager


def cleanup_all_files() -> int:
    """
    清理文件存储目录中的所有文件/目录，并清空 user_latest_files 索引。

    Returns:
        删除的文件/目录数量（粗略计数）
    """

    if not file_storage_dir.exists():
        logger.info("文件存储目录不存在，无需清理")
        return 0

    items = list(file_storage_dir.iterdir())
    if not items:
        logger.info("文件存储目录为空，无需清理")
        return 0

    deleted_count = 0
    for item_path in items:
        try:
            if item_path.is_file():
                item_path.unlink()
                deleted_count += 1
            elif item_path.is_dir():
                shutil.rmtree(item_path)
                deleted_count += 1
        except Exception as e:
            logger.error(f"删除失败 {item_path}: {e}")

    clear_user_latest_files()
    logger.success(f"清理完成，删除了 {deleted_count} 个文件/目录")
    return deleted_count


async def cleanup_with_cache(*, clear_visit_history: bool = False) -> int:
    """
    清理文件并清除内存缓存；可选清空访问历史。
    """
    deleted_count = cleanup_all_files()
    await cache_manager.clear_all()
    if clear_visit_history:
        visit_history_manager.clear()
    return deleted_count


def list_storage_items() -> List[Path]:
    """
    列出存储目录下的所有项目（文件/目录）。
    """
    if not file_storage_dir.exists():
        return []
    return list(file_storage_dir.iterdir())


@dataclass(frozen=True)
class FileStats:
    total_items: int
    file_count: int
    dir_count: int
    total_size_bytes: int
    user_bin_counts: Dict[str, int]


def collect_file_stats() -> FileStats:
    """
    统计存储目录下文件信息。
    """
    items = list_storage_items()
    total_size = 0
    file_count = 0
    dir_count = 0
    user_files: Dict[str, int] = {}

    from ..infra.storage import extract_user_id_from_filename  # 局部导入避免循环

    for item in items:
        try:
            total_size += item.stat().st_size
        except Exception:
            pass

        if item.is_file():
            file_count += 1
            if item.suffix.lower() == ".bin":
                user_id = extract_user_id_from_filename(item.name) or "未知用户"
                user_files[user_id] = user_files.get(user_id, 0) + 1
        elif item.is_dir():
            dir_count += 1

    return FileStats(
        total_items=len(items),
        file_count=file_count,
        dir_count=dir_count,
        total_size_bytes=total_size,
        user_bin_counts=user_files,
    )


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def build_stats_message(stats: FileStats) -> str:
    """
    构造管理员“文件统计”消息文本。
    """
    size_str = format_file_size(stats.total_size_bytes)
    user_stats = "\n".join([f"  - {uid}: {cnt} 个 .bin 文件" for uid, cnt in stats.user_bin_counts.items()]) or "  - （无）"

    return (
        "文件统计信息:\n"
        f"总项目数: {stats.total_items} (文件: {stats.file_count}, 目录: {stats.dir_count})\n"
        f"总大小: {size_str}\n"
        f"按用户 (.bin) 分布:\n{user_stats}"
    )
