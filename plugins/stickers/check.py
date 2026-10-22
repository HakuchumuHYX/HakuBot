# check.py - 查重、清理重复、批量重命名
import asyncio
import time
import uuid
from pathlib import Path
from typing import List, Tuple, Dict, Set
from nonebot import get_driver, logger
from core.lifecycle import on_plugin_startup, runtime
from plugins.stickers.send import (
    sticker_folders,
    sticker_dir,
    resolve_folder_name,
    refresh_max_id,
    invalidate_count_cache,
)
from plugins.stickers.config import IMAGE_EXTENSIONS
from utils.images.dedupe import find_duplicate, find_duplicate_pairs, warm_cache

# ==================== 重复检测 ====================


def _folder_images(folder_path: Path) -> List[Path]:
    """文件夹里的图片，按编号排好（编号小的先入库）"""
    images = [
        file
        for file in folder_path.iterdir()
        if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images, key=_get_sort_key)


def _check_duplicates_sync(
    existing: List[Path], new_images: List[Path]
) -> List[Tuple[Path, Path]]:
    duplicates = []
    for new_img in new_images:
        existing_img = find_duplicate(new_img, existing)
        if existing_img:
            duplicates.append((existing_img, new_img))
    return duplicates


async def check_duplicate_images(
    folder_name: str, new_images: List[Path]
) -> Tuple[bool, List[Tuple[Path, Path]]]:
    """
    检查新图片与文件夹中现有图片是否重复

    返回: (是否有重复, [(已存在图片, 新图片)])
    """
    actual_folder_name = resolve_folder_name(folder_name)
    if actual_folder_name not in sticker_folders:
        return False, []

    existing = _folder_images(sticker_folders[actual_folder_name])
    duplicates = await asyncio.to_thread(_check_duplicates_sync, existing, new_images)
    logger.debug(
        f"查重完成: {folder_name} 现有 {len(existing)} 张，"
        f"新图 {len(new_images)} 张，重复 {len(duplicates)} 张"
    )
    return len(duplicates) > 0, duplicates


WARM_BATCH = 100


async def _warm_dedupe_cache() -> None:
    # 分批进线程：关闭时任务能在两批之间取消，不用等整个图库算完
    started = time.monotonic()
    total = 0
    for folder_path in list(sticker_folders.values()):
        images = _folder_images(folder_path)
        total += len(images)
        for i in range(0, len(images), WARM_BATCH):
            await asyncio.to_thread(warm_cache, images[i : i + WARM_BATCH])
    logger.info(
        f"表情查重哈希预热完成：{total} 张，用时 {time.monotonic() - started:.0f} 秒"
    )


@on_plugin_startup(get_driver(), "stickers")
async def _start_warmup() -> None:
    runtime.spawn(_warm_dedupe_cache(), name="stickers-dedupe-warmup")


async def find_folder_duplicates(folder_name: str) -> List[Tuple[Path, Path]]:
    """查找指定文件夹中的重复图片，每组保留编号最小的那张"""
    actual_folder_name = resolve_folder_name(folder_name)
    if actual_folder_name not in sticker_folders:
        return []

    images = _folder_images(sticker_folders[actual_folder_name])
    duplicates = await asyncio.to_thread(find_duplicate_pairs, images)
    logger.debug(f"在文件夹 {folder_name} 中发现 {len(duplicates)} 组重复图片")
    return duplicates


async def find_all_duplicates() -> Dict[str, List[Tuple[Path, Path]]]:
    """查找所有文件夹中的重复图片"""
    from plugins.stickers.send import get_folder_display_info

    all_duplicates = {}
    folder_info_list = get_folder_display_info()

    for folder_info in folder_info_list:
        folder_name = folder_info["name"]
        duplicates = await find_folder_duplicates(folder_name)
        if duplicates:
            all_duplicates[folder_name] = duplicates

    return all_duplicates


# ==================== 删除/清理函数 ====================


async def safe_remove_duplicates(
    duplicates: Dict[str, List[Tuple[Path, Path]]],
) -> Tuple[int, List[Path]]:
    """安全删除重复图片（移动到备份文件夹）"""
    removed_count = 0
    removed_files = []
    affected_folders: Set[str] = set()

    backup_dir = sticker_dir / "backup_duplicates"
    backup_dir.mkdir(exist_ok=True)

    for folder_name, folder_duplicates in duplicates.items():
        logger.debug(
            f"安全模式：处理文件夹 {folder_name} 的 {len(folder_duplicates)} 组重复图片"
        )

        for existing_img, duplicate_img in folder_duplicates:
            try:
                if (
                    duplicate_img.exists()
                    and existing_img.exists()
                    and duplicate_img != existing_img
                ):
                    # 生成唯一备份路径
                    backup_path = backup_dir / duplicate_img.name
                    counter = 1
                    while backup_path.exists():
                        backup_path = (
                            backup_dir
                            / f"{duplicate_img.stem}_{counter}{duplicate_img.suffix}"
                        )
                        counter += 1

                    duplicate_img.rename(backup_path)
                    removed_files.append(backup_path)
                    removed_count += 1
                    affected_folders.add(folder_name)

            except Exception as e:
                logger.warning(f"安全删除失败 {duplicate_img}: {e}")

    # 使受影响文件夹的计数缓存失效
    for folder_name in affected_folders:
        invalidate_count_cache(folder_name)

    return removed_count, removed_files


# ==================== 批量重命名函数 ====================


def _get_sort_key(file_path: Path) -> Tuple[int, ...]:
    """
    排序键生成函数
    纯数字文件名排在前面，按数值排序
    其他文件名按字母顺序排在后面
    """
    stem = file_path.stem
    if stem.isdigit():
        return (0, int(stem))
    return (1, 0)  # 非数字文件名统一排序权重


def _batch_rename_stickers_sync() -> Tuple[int, str]:
    """
    批量重命名所有贴图文件（同步版本）

    按照 list.json 的文件夹顺序，将所有图片重命名为连续编号

    返回: (重命名数量, 结果消息)
    """
    # 动态导入以获取最新的 folder_configs
    from plugins.stickers.send import folder_configs

    if not folder_configs:
        return 0, "没有找到文件夹配置"

    logger.debug(f"开始批量重命名，共 {len(folder_configs)} 个文件夹配置")

    # 收集所有待处理的文件路径，保持顺序
    all_files_ordered: List[Path] = []

    # 按 list.json 的顺序遍历
    for config in folder_configs:
        folder_name = config["name"]
        folder_path = sticker_dir / folder_name

        if not folder_path.exists():
            logger.debug(f"跳过不存在的文件夹: {folder_path}")
            continue

        # 获取该文件夹下所有图片
        files_in_folder: List[Path] = []
        for file in folder_path.iterdir():
            if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS:
                files_in_folder.append(file)

        # 排序：纯数字文件名按数值排序，其他按字母顺序
        files_in_folder.sort(key=_get_sort_key)

        all_files_ordered.extend(files_in_folder)

    total_files = len(all_files_ordered)
    logger.debug(f"扫描到 {total_files} 张图片待重命名")

    if total_files == 0:
        return 0, "没有找到任何图片"

    # Step 1: 先将所有文件重命名为临时 UUID（避免命名冲突）
    logger.debug("Step 1: 将所有文件重命名为临时 UUID...")
    temp_map: List[Path] = []

    for file_path in all_files_ordered:
        temp_name = f"tmp_{uuid.uuid4()}{file_path.suffix}"
        temp_path = file_path.parent / temp_name

        try:
            file_path.rename(temp_path)
            temp_map.append(temp_path)
        except Exception as e:
            logger.exception(f"重命名临时文件失败 {file_path}: {e}")
            # 尝试回滚已重命名的文件
            return len(temp_map), f"重命名过程中出错: {e}"

    # Step 2: 按顺序赋予新编号
    logger.debug("Step 2: 按顺序赋予新编号...")
    current_id = 1
    renamed_count = 0

    for temp_path in temp_map:
        final_name = f"{current_id}{temp_path.suffix}"
        final_path = temp_path.parent / final_name

        try:
            temp_path.rename(final_path)
            current_id += 1
            renamed_count += 1
        except Exception as e:
            logger.exception(f"重命名最终文件失败 {temp_path} -> {final_name}: {e}")

    logger.info(
        f"批量重命名完成！共重命名 {renamed_count} 张图片（编号 1 至 {current_id - 1}）"
    )

    # 刷新全局编号计数器
    refresh_max_id()

    return (
        renamed_count,
        f"已将 {renamed_count} 张图片重新编号（1 至 {current_id - 1}）",
    )


async def batch_rename_stickers() -> Tuple[int, str]:
    """
    批量重命名所有贴图文件（异步版本）

    返回: (重命名数量, 结果消息)
    """
    return await asyncio.to_thread(_batch_rename_stickers_sync)
