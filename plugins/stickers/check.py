# check.py - 优化版本：使用 dHash + 批量并行处理 + SQLite 缓存
import io
import time
import uuid
import asyncio
import warnings
from pathlib import Path
from typing import List, Tuple, Dict, Set, Optional
from PIL import Image, ImageDraw, ImageFont

# 忽略 PIL 的 EXIF 损坏警告（不影响图片处理）
warnings.filterwarnings("ignore", message="Corrupt EXIF data")
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
from .send import sticker_folders, sticker_dir, resolve_folder_name, count_images_in_folder, refresh_max_id
from .manage import is_superuser
from .config import (
    IMAGE_EXTENSIONS,
    DHASH_SIZE,
    HAMMING_THRESHOLD,
    MSE_THRESHOLD_NUMPY,
    AVG_DIFF_THRESHOLD_PYTHON,
    BATCH_SIZE,
)
from .cache_db import (
    get_cached_hash,
    update_cache,
    invalidate_cache,
    clear_all_cache,
    get_cache_stats,
    migrate_from_json,
)

# numpy 可选，用于加速像素比较
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("numpy 未安装，将使用纯 Python 模式进行图片比较")

# 启动时尝试迁移旧的 JSON 缓存
try:
    migrate_from_json()
except Exception as e:
    logger.warning(f"迁移 JSON 缓存时出错: {e}")


# ==================== dHash 算法实现 ====================

def calculate_dhash(image_path: Path) -> str:
    """
    计算图片的差异哈希 (dHash)
    
    算法步骤:
    1. 缩放到 (DHASH_SIZE+1) x DHASH_SIZE 灰度图
    2. 比较每行相邻像素，左边 > 右边 = 1，否则 = 0
    3. 产生 DHASH_SIZE * DHASH_SIZE 位的哈希
    """
    # 先检查缓存
    cached = get_cached_hash(image_path)
    if cached:
        return cached

    try:
        with Image.open(image_path) as img:
            # 转为灰度
            if img.mode != 'L':
                img = img.convert('L')

            # 缩放到 (size+1) x size
            img = img.resize((DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS)

            # 获取像素
            pixels = list(img.getdata())

            # 计算差异哈希
            hash_bits = []
            for row in range(DHASH_SIZE):
                for col in range(DHASH_SIZE):
                    left_idx = row * (DHASH_SIZE + 1) + col
                    right_idx = left_idx + 1
                    hash_bits.append('1' if pixels[left_idx] > pixels[right_idx] else '0')

            dhash = ''.join(hash_bits)

            # 更新缓存
            update_cache(image_path, dhash)

            return dhash

    except Exception as e:
        logger.error(f"计算 dHash 失败 {image_path}: {e}")
        return ""


def hamming_distance(hash1: str, hash2: str) -> int:
    """计算两个哈希的汉明距离"""
    if len(hash1) != len(hash2):
        return 999  # 无效比较
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


# ==================== 批量并行处理 ====================

async def batch_calculate_hashes(image_files: List[Path]) -> Dict[Path, str]:
    """
    批量并行计算图片哈希
    
    返回: {图片路径: dhash}
    """
    results: Dict[Path, str] = {}
    total = len(image_files)

    logger.info(f"开始批量计算哈希，共 {total} 张图片")
    start_time = time.time()

    # 分批处理
    for i in range(0, total, BATCH_SIZE):
        batch = image_files[i:i + BATCH_SIZE]

        # 创建任务
        tasks = [asyncio.to_thread(calculate_dhash, f) for f in batch]

        # 并行执行
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 收集结果
        for path, result in zip(batch, batch_results):
            if isinstance(result, Exception):
                logger.warning(f"计算哈希异常 {path}: {result}")
            elif result:
                results[path] = result

        # 进度日志
        processed = min(i + BATCH_SIZE, total)
        if processed % 500 == 0 or processed == total:
            elapsed = time.time() - start_time
            logger.info(f"哈希计算进度: {processed}/{total} ({elapsed:.1f}s)")

    elapsed = time.time() - start_time
    logger.info(f"哈希计算完成，耗时 {elapsed:.1f}s，有效结果 {len(results)} 个")

    return results


# ==================== 重复检测核心函数 ====================

async def check_duplicate_images(folder_name: str, new_images: List[Path]) -> Tuple[bool, List[Tuple[Path, Path]]]:
    """
    检查新图片与文件夹中现有图片是否重复
    
    返回: (是否有重复, [(已存在图片, 新图片)])
    """
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return False, []

    folder_path = sticker_folders[actual_folder_name]

    # 收集现有图片
    existing_images: Set[Path] = set()

    for ext in IMAGE_EXTENSIONS:
        existing_images.update(folder_path.glob(f"*{ext}"))
        existing_images.update(folder_path.glob(f"*{ext.upper()}"))

    existing_list = list(existing_images)

    logger.info(f"开始查重: {folder_name}, 现有图片: {len(existing_list)}, 新图片: {len(new_images)}")

    if not existing_list:
        return False, []

    # 批量计算现有图片的哈希
    existing_hashes = await batch_calculate_hashes(existing_list)

    # 构建哈希索引 {dhash: path}
    hash_to_path: Dict[str, Path] = {}
    for path, dhash in existing_hashes.items():
        if dhash:
            hash_to_path[dhash] = path

    # 检查新图片
    duplicates: List[Tuple[Path, Path]] = []

    for new_img in new_images:
        new_hash = await asyncio.to_thread(calculate_dhash, new_img)
        if not new_hash:
            continue

        # 查找相似的已存在图片
        for existing_hash, existing_path in hash_to_path.items():
            distance = hamming_distance(new_hash, existing_hash)
            if distance <= HAMMING_THRESHOLD:
                # 找到相似图片，进行最终验证
                if await verify_duplicate(existing_path, new_img):
                    duplicates.append((existing_path, new_img))
                    logger.info(f"发现重复: {existing_path.name} <-> {new_img.name} (距离: {distance})")
                    break  # 找到一个重复即可

    logger.info(f"查重完成: 检查了 {len(new_images)} 张新图片，发现 {len(duplicates)} 个重复")

    return len(duplicates) > 0, duplicates


async def find_folder_duplicates(folder_name: str) -> List[Tuple[Path, Path]]:
    """
    查找指定文件夹中的重复图片
    """
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return []

    folder_path = sticker_folders[actual_folder_name]

    # 收集所有图片
    image_files: Set[Path] = set()

    for ext in IMAGE_EXTENSIONS:
        image_files.update(folder_path.glob(f"*{ext}"))
        image_files.update(folder_path.glob(f"*{ext.upper()}"))

    image_list = list(image_files)

    logger.info(f"在文件夹 {folder_name} 中找到 {len(image_list)} 张图片")

    if len(image_list) < 2:
        return []

    # 批量计算哈希
    all_hashes = await batch_calculate_hashes(image_list)

    # 按哈希分组，查找相似图片
    duplicates: List[Tuple[Path, Path]] = []
    processed: Set[Path] = set()

    # 转换为列表便于遍历
    hash_items = [(path, dhash) for path, dhash in all_hashes.items() if dhash]

    for i, (path1, hash1) in enumerate(hash_items):
        if path1 in processed:
            continue

        for j in range(i + 1, len(hash_items)):
            path2, hash2 = hash_items[j]

            if path2 in processed:
                continue

            # 计算汉明距离
            distance = hamming_distance(hash1, hash2)

            if distance <= HAMMING_THRESHOLD:
                # 相似，进行最终验证
                if await verify_duplicate(path1, path2):
                    duplicates.append((path1, path2))
                    processed.add(path2)  # 标记为已处理
                    logger.info(f"发现重复: {path1.name} <-> {path2.name} (距离: {distance})")

    logger.info(f"在文件夹 {folder_name} 中发现 {len(duplicates)} 组重复图片")
    return duplicates


async def find_all_duplicates() -> Dict[str, List[Tuple[Path, Path]]]:
    """查找所有文件夹中的重复图片"""
    from .send import get_folder_display_info

    all_duplicates = {}
    folder_info_list = get_folder_display_info()

    for folder_info in folder_info_list:
        folder_name = folder_info["name"]
        duplicates = await find_folder_duplicates(folder_name)
        if duplicates:
            all_duplicates[folder_name] = duplicates

    return all_duplicates


# ==================== 验证函数 ====================

def _verify_duplicate_sync(img1: Path, img2: Path) -> bool:
    """
    验证两张图片是否真的是重复（像素级比较）
    """
    if img1 == img2:
        return False

    try:
        with Image.open(img1) as image1, Image.open(img2) as image2:
            # 转换为 RGB
            if image1.mode != 'RGB':
                image1 = image1.convert('RGB')
            if image2.mode != 'RGB':
                image2 = image2.convert('RGB')

            # 统一尺寸
            size = (100, 100)
            image1 = image1.resize(size, Image.Resampling.LANCZOS)
            image2 = image2.resize(size, Image.Resampling.LANCZOS)

            if HAS_NUMPY:
                arr1 = np.array(image1)
                arr2 = np.array(image2)
                mse = np.mean((arr1.astype(float) - arr2.astype(float)) ** 2)
                return mse < MSE_THRESHOLD_NUMPY
            else:
                pixels1 = list(image1.getdata())
                pixels2 = list(image2.getdata())

                if len(pixels1) != len(pixels2):
                    return False

                total_diff = 0
                for p1, p2 in zip(pixels1, pixels2):
                    if isinstance(p1, int):
                        total_diff += abs(p1 - p2)
                    else:
                        total_diff += sum(abs(c1 - c2) for c1, c2 in zip(p1, p2))

                avg_diff = total_diff / len(pixels1)
                return avg_diff < AVG_DIFF_THRESHOLD_PYTHON

    except Exception as e:
        logger.error(f"验证重复图片失败 {img1} vs {img2}: {e}")
        return False


async def verify_duplicate(img1: Path, img2: Path) -> bool:
    """异步包装器"""
    return await asyncio.to_thread(_verify_duplicate_sync, img1, img2)


# ==================== 删除/清理函数 ====================

async def remove_duplicates(duplicates: Dict[str, List[Tuple[Path, Path]]]) -> int:
    """删除重复图片"""
    removed_count = 0

    for folder_name, folder_duplicates in duplicates.items():
        for existing_img, duplicate_img in folder_duplicates:
            try:
                if (duplicate_img.exists() and
                        existing_img.exists() and
                        duplicate_img != existing_img):
                    backup_path = duplicate_img.with_suffix(duplicate_img.suffix + '.bak')
                    duplicate_img.rename(backup_path)
                    removed_count += 1
                    # 清除缓存
                    invalidate_cache(duplicate_img)

            except Exception as e:
                logger.error(f"删除重复图片失败 {duplicate_img}: {e}")

    return removed_count


async def safe_remove_duplicates(duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Tuple[int, List[Path]]:
    """安全删除重复图片（移动到备份文件夹）"""
    removed_count = 0
    removed_files = []

    backup_dir = sticker_dir / "backup_duplicates"
    backup_dir.mkdir(exist_ok=True)

    for folder_name, folder_duplicates in duplicates.items():
        logger.info(f"安全模式：处理文件夹 {folder_name} 的 {len(folder_duplicates)} 组重复图片")

        for existing_img, duplicate_img in folder_duplicates:
            try:
                if (duplicate_img.exists() and
                        existing_img.exists() and
                        duplicate_img != existing_img):

                    # 生成唯一备份路径
                    backup_path = backup_dir / duplicate_img.name
                    counter = 1
                    while backup_path.exists():
                        backup_path = backup_dir / f"{duplicate_img.stem}_{counter}{duplicate_img.suffix}"
                        counter += 1

                    duplicate_img.rename(backup_path)
                    removed_files.append(backup_path)
                    removed_count += 1

                    # 清除缓存
                    invalidate_cache(duplicate_img)

            except Exception as e:
                logger.error(f"安全删除失败 {duplicate_img}: {e}")

    return removed_count, removed_files


# ==================== 报告渲染函数 ====================

def _render_duplicate_report_sync(folder_name: str, duplicates: List[Tuple[Path, Path]]) -> Optional[bytes]:
    """渲染重复图片报告"""
    try:
        if not duplicates:
            return None

        items_per_row = 1
        rows = min(len(duplicates), 10)  # 最多显示10组

        preview_width = 280
        preview_height = 200
        cell_width = preview_width * 2 + 60
        cell_height = preview_height + 120

        padding = 30
        spacing = 20
        img_width = items_per_row * cell_width + 2 * padding
        img_height = rows * cell_height + (rows - 1) * spacing + 2 * padding + 120

        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        # 加载字体
        try:
            title_font = ImageFont.truetype("msyh.ttc", 32)
            subtitle_font = ImageFont.truetype("msyh.ttc", 24)
            text_font = ImageFont.truetype("msyh.ttc", 18)
            small_font = ImageFont.truetype("msyh.ttc", 14)
        except:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 32)
                subtitle_font = ImageFont.truetype("simhei.ttf", 24)
                text_font = ImageFont.truetype("simhei.ttf", 18)
                small_font = ImageFont.truetype("simhei.ttf", 14)
            except:
                title_font = ImageFont.load_default()
                subtitle_font = title_font
                text_font = title_font
                small_font = title_font

        # 标题
        title = f"图片重复检测 - {folder_name}"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((img_width - title_width) // 2, padding), title, fill=(231, 76, 60), font=title_font)

        # 说明
        info_text = f"检测到 {len(duplicates)} 张重复图片"
        info_bbox = draw.textbbox((0, 0), info_text, font=subtitle_font)
        draw.text(((img_width - (info_bbox[2] - info_bbox[0])) // 2, padding + 45), info_text, fill=(44, 62, 80), font=subtitle_font)

        # 提示
        action_text = "如需强制上传，请在投稿命令后加 \"force\""
        action_bbox = draw.textbbox((0, 0), action_text, font=text_font)
        draw.text(((img_width - (action_bbox[2] - action_bbox[0])) // 2, padding + 80), action_text, fill=(230, 126, 34), font=text_font)

        # 绘制重复图片对比
        start_y = padding + 130

        for i, (existing_img, new_img) in enumerate(duplicates[:10]):
            x = padding
            y = start_y + i * (cell_height + spacing)

            # 背景
            draw.rounded_rectangle([x, y, x + cell_width, y + cell_height], radius=8, fill=(255, 255, 255), outline=(231, 76, 60), width=2)

            separator_x = x + cell_width // 2
            draw.line([(separator_x, y + 30), (separator_x, y + cell_height - 30)], fill=(225, 232, 237), width=2)

            # 左侧：已有图片
            left_label = "已有图片"
            draw.text((x + (cell_width // 2 - draw.textbbox((0, 0), left_label, font=text_font)[2]) // 2, y + 10), left_label, fill=(52, 152, 219), font=text_font)

            try:
                left_preview = Image.open(existing_img)
                if left_preview.mode != 'RGB':
                    left_preview = left_preview.convert('RGB')
                left_preview.thumbnail((preview_width - 20, preview_height), Image.Resampling.LANCZOS)
                left_x = x + (cell_width // 2 - left_preview.width) // 2
                img.paste(left_preview, (left_x, y + 35))

                left_filename = existing_img.name[:20] + "..." if len(existing_img.name) > 20 else existing_img.name
                fname_bbox = draw.textbbox((0, 0), left_filename, font=small_font)
                draw.text((x + (cell_width // 2 - (fname_bbox[2] - fname_bbox[0])) // 2, y + 35 + preview_height + 10), left_filename, fill=(127, 140, 141), font=small_font)
            except:
                draw.text((x + 20, y + 100), "加载失败", fill=(200, 0, 0), font=small_font)

            # 右侧：投稿图片
            right_label = "投稿图片"
            draw.text((separator_x + (cell_width // 2 - draw.textbbox((0, 0), right_label, font=text_font)[2]) // 2, y + 10), right_label, fill=(231, 76, 60), font=text_font)

            try:
                right_preview = Image.open(new_img)
                if right_preview.mode != 'RGB':
                    right_preview = right_preview.convert('RGB')
                right_preview.thumbnail((preview_width - 20, preview_height), Image.Resampling.LANCZOS)
                right_x = separator_x + (cell_width // 2 - right_preview.width) // 2
                img.paste(right_preview, (right_x, y + 35))

                right_filename = new_img.name[:20] + "..." if len(new_img.name) > 20 else new_img.name
                fname_bbox = draw.textbbox((0, 0), right_filename, font=small_font)
                draw.text((separator_x + (cell_width // 2 - (fname_bbox[2] - fname_bbox[0])) // 2, y + 35 + preview_height + 10), right_filename, fill=(127, 140, 141), font=small_font)
            except:
                draw.text((separator_x + 20, y + 100), "加载失败", fill=(200, 0, 0), font=small_font)

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        logger.error(f"生成重复报告图片失败: {e}")
        return None


async def render_duplicate_report(folder_name: str, duplicates: List[Tuple[Path, Path]]) -> Optional[bytes]:
    """异步包装器"""
    return await asyncio.to_thread(_render_duplicate_report_sync, folder_name, duplicates)


def _preview_duplicates_before_cleanup_sync(all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Optional[bytes]:
    """预览将要删除的重复图片"""
    try:
        if not all_duplicates:
            return None

        total_pairs = sum(len(dups) for dups in all_duplicates.values())

        img_width = 600
        img_height = 400
        padding = 30

        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("msyh.ttc", 32)
            text_font = ImageFont.truetype("msyh.ttc", 20)
            small_font = ImageFont.truetype("msyh.ttc", 16)
        except:
            title_font = ImageFont.load_default()
            text_font = title_font
            small_font = title_font

        title = "重复图片清理预览"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((img_width - (title_bbox[2] - title_bbox[0])) // 2, padding), title, fill=(231, 76, 60), font=title_font)

        warning_text = "警告：即将删除以下重复图片"
        warning_bbox = draw.textbbox((0, 0), warning_text, font=text_font)
        draw.text(((img_width - (warning_bbox[2] - warning_bbox[0])) // 2, padding + 50), warning_text, fill=(231, 76, 60), font=text_font)

        stats_text = f"检测到 {total_pairs} 组重复图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=text_font)
        draw.text(((img_width - (stats_bbox[2] - stats_bbox[0])) // 2, padding + 90), stats_text, fill=(44, 62, 80), font=text_font)

        y_pos = padding + 140
        for folder_name, duplicates in all_duplicates.items():
            folder_text = f"{folder_name}: {len(duplicates)} 组重复"
            draw.text((padding, y_pos), folder_text, fill=(127, 140, 141), font=small_font)
            y_pos += 25

        confirm_text = "请回复『确认清理』执行清理，或『取消』取消操作"
        confirm_bbox = draw.textbbox((0, 0), confirm_text, font=text_font)
        draw.text(((img_width - (confirm_bbox[2] - confirm_bbox[0])) // 2, img_height - padding - 40), confirm_text, fill=(231, 76, 60), font=text_font)

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        logger.error(f"生成清理预览图片失败: {e}")
        return None


async def preview_duplicates_before_cleanup(all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Optional[bytes]:
    """异步包装器"""
    return await asyncio.to_thread(_preview_duplicates_before_cleanup_sync, all_duplicates)


def _render_cleanup_report_sync(removed_count: int, all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Optional[bytes]:
    """渲染清理结果报告"""
    try:
        total_pairs = sum(len(dups) for dups in all_duplicates.values())

        img_width = 600
        img_height = 400
        padding = 30

        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        try:
            title_font = ImageFont.truetype("msyh.ttc", 32)
            text_font = ImageFont.truetype("msyh.ttc", 20)
            small_font = ImageFont.truetype("msyh.ttc", 16)
        except:
            title_font = ImageFont.load_default()
            text_font = title_font
            small_font = title_font

        title = "重复图片清理报告"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((img_width - (title_bbox[2] - title_bbox[0])) // 2, padding), title, fill=(39, 174, 96), font=title_font)

        stats_text = f"检测到 {total_pairs} 组重复图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=text_font)
        draw.text(((img_width - (stats_bbox[2] - stats_bbox[0])) // 2, padding + 50), stats_text, fill=(44, 62, 80), font=text_font)

        result_text = f"已清理 {removed_count} 张重复图片"
        result_bbox = draw.textbbox((0, 0), result_text, font=text_font)
        draw.text(((img_width - (result_bbox[2] - result_bbox[0])) // 2, padding + 90), result_text, fill=(39, 174, 96), font=text_font)

        y_pos = padding + 140
        for folder_name, duplicates in all_duplicates.items():
            folder_text = f"{folder_name}: {len(duplicates)} 组重复"
            draw.text((padding, y_pos), folder_text, fill=(127, 140, 141), font=small_font)
            y_pos += 25

        footer_text = "重复图片已移动到备份文件夹"
        footer_bbox = draw.textbbox((0, 0), footer_text, font=small_font)
        draw.text(((img_width - (footer_bbox[2] - footer_bbox[0])) // 2, img_height - padding - 20), footer_text, fill=(127, 140, 141), font=small_font)

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        logger.error(f"生成清理报告图片失败: {e}")
        return None


async def render_cleanup_report(removed_count: int, all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Optional[bytes]:
    """异步包装器"""
    return await asyncio.to_thread(_render_cleanup_report_sync, removed_count, all_duplicates)


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
    from .send import folder_configs
    
    if not folder_configs:
        return 0, "没有找到文件夹配置"
    
    logger.info(f"开始批量重命名，共 {len(folder_configs)} 个文件夹配置")
    
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
    logger.info(f"扫描到 {total_files} 张图片待重命名")
    
    if total_files == 0:
        return 0, "没有找到任何图片"
    
    # Step 1: 先将所有文件重命名为临时 UUID（避免命名冲突）
    logger.info("Step 1: 将所有文件重命名为临时 UUID...")
    temp_map: List[Path] = []
    
    for file_path in all_files_ordered:
        temp_name = f"tmp_{uuid.uuid4()}{file_path.suffix}"
        temp_path = file_path.parent / temp_name
        
        try:
            file_path.rename(temp_path)
            temp_map.append(temp_path)
            # 清除旧路径的缓存
            invalidate_cache(file_path)
        except Exception as e:
            logger.error(f"重命名临时文件失败 {file_path}: {e}")
            # 尝试回滚已重命名的文件
            return len(temp_map), f"重命名过程中出错: {e}"
    
    # Step 2: 按顺序赋予新编号
    logger.info("Step 2: 按顺序赋予新编号...")
    current_id = 1
    renamed_count = 0
    
    for temp_path in temp_map:
        final_name = f"{current_id}{temp_path.suffix}"
        final_path = temp_path.parent / final_name
        
        try:
            temp_path.rename(final_path)
            current_id += 1
            renamed_count += 1
            # 清除临时路径的缓存
            invalidate_cache(temp_path)
        except Exception as e:
            logger.error(f"重命名最终文件失败 {temp_path} -> {final_name}: {e}")
    
    logger.info(f"批量重命名完成！共重命名 {renamed_count} 张图片（编号 1 至 {current_id - 1}）")
    
    # 刷新全局编号计数器
    refresh_max_id()
    
    return renamed_count, f"已将 {renamed_count} 张图片重新编号（1 至 {current_id - 1}）"


async def batch_rename_stickers() -> Tuple[int, str]:
    """
    批量重命名所有贴图文件（异步版本）
    
    返回: (重命名数量, 结果消息)
    """
    return await asyncio.to_thread(_batch_rename_stickers_sync)
