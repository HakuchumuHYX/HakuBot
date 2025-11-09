# check.py - 带缓存机制的版本
import hashlib
import io
import json
import time
from pathlib import Path
from typing import List, Tuple, Dict, Set
from PIL import Image, ImageDraw, ImageFont
from nonebot.adapters.onebot.v11 import MessageSegment

from .send import sticker_folders, sticker_dir, resolve_folder_name, count_images_in_folder
from .manage import is_superuser

# 添加缺失的导入
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    print("警告: numpy 未安装，将使用纯Python模式进行图片比较")

# 缓存文件路径
CACHE_FILE = sticker_dir / "hash_cache.json"
# 缓存版本，当算法改变时更新此版本号
CACHE_VERSION = "2.0"
# 缓存有效期（秒），30天
CACHE_TTL = 30 * 24 * 60 * 60

# 全局缓存字典
_hash_cache = None


def load_hash_cache() -> Dict:
    """
    加载哈希缓存
    """
    global _hash_cache

    if _hash_cache is not None:
        return _hash_cache

    if not CACHE_FILE.exists():
        _hash_cache = {
            "version": CACHE_VERSION,
            "created_at": time.time(),
            "last_cleanup": time.time(),
            "entries": {}
        }
        return _hash_cache

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)

        # 检查缓存版本
        if cache_data.get("version") != CACHE_VERSION:
            print("缓存版本不匹配，创建新缓存")
            _hash_cache = {
                "version": CACHE_VERSION,
                "created_at": time.time(),
                "last_cleanup": time.time(),
                "entries": {}
            }
        else:
            _hash_cache = cache_data

        # 定期清理过期缓存
        current_time = time.time()
        if current_time - _hash_cache.get("last_cleanup", 0) > 24 * 60 * 60:  # 每天清理一次
            cleanup_expired_cache()

    except Exception as e:
        print(f"加载哈希缓存失败: {e}，创建新缓存")
        _hash_cache = {
            "version": CACHE_VERSION,
            "created_at": time.time(),
            "last_cleanup": time.time(),
            "entries": {}
        }

    return _hash_cache


def save_hash_cache():
    """
    保存哈希缓存到文件
    """
    global _hash_cache

    if _hash_cache is None:
        return

    try:
        # 确保目录存在
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_hash_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存哈希缓存失败: {e}")


def get_cache_key(image_path: Path) -> str:
    """
    生成缓存键：文件路径 + 文件大小 + 修改时间
    """
    try:
        stat = image_path.stat()
        return f"{image_path.absolute()}:{stat.st_size}:{stat.st_mtime}"
    except:
        return str(image_path.absolute())


def get_cached_hash(image_path: Path, hash_type: str) -> Tuple[str, bool]:
    """
    从缓存获取哈希值

    返回: (哈希值, 是否来自缓存)
    """
    if not image_path.exists():
        return "", False

    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)

    entry = cache["entries"].get(cache_key)
    if not entry:
        return "", False

    # 检查缓存是否过期
    current_time = time.time()
    if current_time - entry.get("timestamp", 0) > CACHE_TTL:
        # 删除过期缓存
        del cache["entries"][cache_key]
        return "", False

    # 返回缓存的哈希值
    return entry.get(hash_type, ""), True


def update_hash_cache(image_path: Path, perceptual_hash: str, file_hash: str):
    """
    更新缓存中的哈希值
    """
    if not image_path.exists():
        return

    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)

    cache["entries"][cache_key] = {
        "perceptual_hash": perceptual_hash,
        "file_hash": file_hash,
        "timestamp": time.time(),
        "file_size": image_path.stat().st_size,
        "file_mtime": image_path.stat().st_mtime
    }

    # 限制缓存大小，避免无限增长
    if len(cache["entries"]) > 10000:  # 最多缓存10000个文件
        # 删除最旧的缓存条目
        oldest_entries = sorted(
            cache["entries"].items(),
            key=lambda x: x[1].get("timestamp", 0)
        )[:1000]  # 删除1000个最旧的
        for key, _ in oldest_entries:
            del cache["entries"][key]

    save_hash_cache()


def cleanup_expired_cache():
    """
    清理过期缓存
    """
    global _hash_cache

    cache = load_hash_cache()
    current_time = time.time()
    expired_keys = []

    for key, entry in cache["entries"].items():
        if current_time - entry.get("timestamp", 0) > CACHE_TTL:
            expired_keys.append(key)

    for key in expired_keys:
        del cache["entries"][key]

    cache["last_cleanup"] = current_time
    print(f"清理了 {len(expired_keys)} 个过期缓存条目")
    save_hash_cache()


def invalidate_cache_for_file(image_path: Path):
    """
    使指定文件的缓存失效
    """
    global _hash_cache

    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)

    if cache_key in cache["entries"]:
        del cache["entries"][cache_key]
        save_hash_cache()


def clear_cache():
    """
    清空整个缓存
    """
    global _hash_cache

    _hash_cache = {
        "version": CACHE_VERSION,
        "created_at": time.time(),
        "last_cleanup": time.time(),
        "entries": {}
    }
    save_hash_cache()
    print("哈希缓存已清空")


def get_cache_stats() -> Dict:
    """
    获取缓存统计信息
    """
    cache = load_hash_cache()
    return {
        "version": cache.get("version", "unknown"),
        "entries_count": len(cache.get("entries", {})),
        "created_at": cache.get("created_at", 0),
        "last_cleanup": cache.get("last_cleanup", 0),
        "cache_size_mb": CACHE_FILE.stat().st_size / 1024 / 1024 if CACHE_FILE.exists() else 0
    }


def calculate_image_hash(image_path: Path) -> str:
    """
    计算图片的MD5哈希值（带缓存版本）
    """
    # 先尝试从缓存获取
    cached_hash, from_cache = get_cached_hash(image_path, "perceptual_hash")
    if from_cache:
        return cached_hash

    try:
        # 使用PIL打开图片
        with Image.open(image_path) as img:
            # 转换为RGB模式，统一格式
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # 调整到固定尺寸，避免尺寸差异
            img = img.resize((64, 64), Image.Resampling.LANCZOS)

            # 转换为灰度图，减少颜色信息的影响
            img = img.convert('L')

            # 获取像素数据
            pixels = list(img.getdata())

            # 计算平均值
            avg = sum(pixels) / len(pixels)

            # 生成哈希：将每个像素与平均值比较
            hash_str = ''.join('1' if pixel > avg else '0' for pixel in pixels)

            # 返回哈希的MD5，避免过长的字符串
            perceptual_hash = hashlib.md5(hash_str.encode()).hexdigest()

            # 同时计算文件哈希用于缓存
            file_hash = calculate_image_hash_simple(image_path)

            # 更新缓存
            update_hash_cache(image_path, perceptual_hash, file_hash)

            return perceptual_hash

    except Exception as e:
        print(f"计算图片哈希失败 {image_path}: {e}")
        return ""


def calculate_image_hash_simple(image_path: Path) -> str:
    """
    简单的文件哈希计算（带缓存版本）
    """
    # 先尝试从缓存获取
    cached_hash, from_cache = get_cached_hash(image_path, "file_hash")
    if from_cache:
        return cached_hash

    try:
        with open(image_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()

        # 获取感知哈希用于缓存（如果可能）
        perceptual_hash = ""
        try:
            with Image.open(image_path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img = img.resize((64, 64), Image.Resampling.LANCZOS)
                img = img.convert('L')
                pixels = list(img.getdata())
                avg = sum(pixels) / len(pixels)
                hash_str = ''.join('1' if pixel > avg else '0' for pixel in pixels)
                perceptual_hash = hashlib.md5(hash_str.encode()).hexdigest()
        except:
            pass

        # 更新缓存
        update_hash_cache(image_path, perceptual_hash, file_hash)

        return file_hash
    except Exception as e:
        print(f"计算文件哈希失败 {image_path}: {e}")
        return ""


async def check_duplicate_images(folder_name: str, new_images: List[Path]) -> Tuple[bool, List[Tuple[Path, Path]]]:
    """
    检查新图片与文件夹中现有图片是否重复（带缓存版本）
    """
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return False, []

    folder_path = sticker_folders[actual_folder_name]

    # 获取文件夹中所有现有图片
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    existing_images_set: Set[Path] = set()

    for ext in image_extensions:
        for file in folder_path.glob(f"*{ext}"):
            existing_images_set.add(file)
        for file in folder_path.glob(f"*{ext.upper()}"):
            existing_images_set.add(file)

    existing_images = list(existing_images_set)

    print(f"开始查重: {folder_name}, 现有图片: {len(existing_images)}, 新图片: {len(new_images)}")

    # 使用缓存计算现有图片的哈希值
    existing_perceptual_hashes = {}
    existing_file_hashes = {}

    for img_path in existing_images:
        perceptual_hash = calculate_image_hash(img_path)
        file_hash = calculate_image_hash_simple(img_path)
        if perceptual_hash and file_hash:
            existing_perceptual_hashes[perceptual_hash] = img_path
            existing_file_hashes[img_path] = file_hash

    # 检查新图片是否有重复
    duplicates = []
    cache_hits = 0
    total_checked = 0

    for new_img in new_images:
        total_checked += 1
        new_perceptual_hash = calculate_image_hash(new_img)
        new_file_hash = calculate_image_hash_simple(new_img)

        if new_perceptual_hash and new_file_hash:
            if new_perceptual_hash in existing_perceptual_hashes:
                existing_img = existing_perceptual_hashes[new_perceptual_hash]
                # 双重验证
                if new_file_hash == existing_file_hashes.get(existing_img, ""):
                    if await verify_duplicate(existing_img, new_img):
                        duplicates.append((existing_img, new_img))

    print(f"查重完成: 检查了 {total_checked} 张图片，发现 {len(duplicates)} 个重复")

    return len(duplicates) > 0, duplicates


# check.py - 修改 render_duplicate_report 函数

async def render_duplicate_report(folder_name: str, duplicates: List[Tuple[Path, Path]]) -> bytes:
    """
    渲染重复图片报告为图片 - 改进版本：左右对比显示

    返回: 图片的bytes数据
    """
    try:
        if not duplicates:
            return None

        # 计算布局 - 每行显示1组重复对比，因为需要左右对比
        items_per_row = 1  # 改为每行1组，便于左右对比
        rows = len(duplicates)

        # 图片预览尺寸
        preview_width = 280  # 增加宽度以适应左右布局
        preview_height = 200

        # 单元格尺寸（包含左右两张图片和文字）
        cell_width = preview_width * 2 + 60  # 两张图片宽度 + 间距
        cell_height = preview_height + 120  # 增加高度以容纳更多信息

        # 计算总尺寸
        padding = 30
        spacing = 20
        img_width = items_per_row * cell_width + (items_per_row - 1) * spacing + 2 * padding
        img_height = rows * cell_height + (rows - 1) * spacing + 2 * padding + 120  # 增加标题区域高度

        # 创建画布
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
                subtitle_font = ImageFont.load_default()
                text_font = ImageFont.load_default()
                small_font = ImageFont.load_default()

        # 绘制标题
        title = f"图片重复检测 - {folder_name}"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img_width - title_width) // 2
        draw.text((title_x, padding), title, fill=(231, 76, 60), font=title_font)

        # 绘制说明
        info_text = f"检测到 {len(duplicates)} 张重复图片"
        info_bbox = draw.textbbox((0, 0), info_text, font=subtitle_font)
        info_width = info_bbox[2] - info_bbox[0]
        info_x = (img_width - info_width) // 2
        draw.text((info_x, padding + 45), info_text, fill=(44, 62, 80), font=subtitle_font)

        # 绘制操作提示
        action_text = "如遇查重错误，需强制上传，请在投稿命令后加上\"force\""
        action_bbox = draw.textbbox((0, 0), action_text, font=text_font)
        action_width = action_bbox[2] - action_bbox[0]
        action_x = (img_width - action_width) // 2
        draw.text((action_x, padding + 80), action_text, fill=(230, 126, 34), font=text_font)

        # 绘制重复图片对比
        start_y = padding + 130

        for i, (existing_img, new_img) in enumerate(duplicates):
            row = i // items_per_row
            col = i % items_per_row

            # 计算单元格位置
            x = padding + col * (cell_width + spacing)
            y = start_y + row * (cell_height + spacing)

            # 绘制单元格背景
            draw.rounded_rectangle(
                [x, y, x + cell_width, y + cell_height],
                radius=8,
                fill=(255, 255, 255),
                outline=(231, 76, 60),
                width=2
            )

            # 绘制分隔线
            separator_x = x + cell_width // 2
            draw.line([(separator_x, y + 30), (separator_x, y + cell_height - 30)],
                      fill=(225, 232, 237), width=2)

            # 绘制左侧：已有图片
            left_label = "已有图片"
            left_bbox = draw.textbbox((0, 0), left_label, font=text_font)
            left_label_width = left_bbox[2] - left_bbox[0]
            left_label_x = x + (cell_width // 2 - left_label_width) // 2
            draw.text((left_label_x, y + 10), left_label, fill=(52, 152, 219), font=text_font)

            # 绘制左侧图片预览
            try:
                left_preview = Image.open(existing_img)
                if left_preview.mode != 'RGB':
                    left_preview = left_preview.convert('RGB')

                # 缩放图片
                left_preview.thumbnail((preview_width - 20, preview_height), Image.Resampling.LANCZOS)
                left_preview_x = x + (cell_width // 2 - left_preview.width) // 2
                left_preview_y = y + 35
                img.paste(left_preview, (left_preview_x, left_preview_y))

                # 显示左侧文件名
                left_filename = existing_img.name
                if len(left_filename) > 20:
                    left_filename = left_filename[:17] + "..."
                left_filename_bbox = draw.textbbox((0, 0), left_filename, font=small_font)
                left_filename_width = left_filename_bbox[2] - left_filename_bbox[0]
                left_filename_x = x + (cell_width // 2 - left_filename_width) // 2
                draw.text((left_filename_x, y + 35 + preview_height + 10), left_filename,
                          fill=(127, 140, 141), font=small_font)

            except Exception as e:
                # 绘制占位符
                placeholder_size = 60
                placeholder_x = x + (cell_width // 2 - placeholder_size) // 2
                placeholder_y = y + 35 + (preview_height - placeholder_size) // 2

                draw.rounded_rectangle(
                    [placeholder_x, placeholder_y, placeholder_x + placeholder_size, placeholder_y + placeholder_size],
                    radius=5,
                    fill=(225, 232, 237)
                )

                error_text = "加载失败"
                error_bbox = draw.textbbox((0, 0), error_text, font=small_font)
                error_width = error_bbox[2] - error_bbox[0]
                error_x = placeholder_x + (placeholder_size - error_width) // 2
                error_y = placeholder_y + (placeholder_size - small_font.size) // 2
                draw.text((error_x, error_y), error_text, fill=(127, 140, 141), font=small_font)

            # 绘制右侧：投稿图片
            right_label = "投稿图片"
            right_bbox = draw.textbbox((0, 0), right_label, font=text_font)
            right_label_width = right_bbox[2] - right_bbox[0]
            right_label_x = separator_x + (cell_width // 2 - right_label_width) // 2
            draw.text((right_label_x, y + 10), right_label, fill=(231, 76, 60), font=text_font)

            # 绘制右侧图片预览
            try:
                right_preview = Image.open(new_img)
                if right_preview.mode != 'RGB':
                    right_preview = right_preview.convert('RGB')

                # 缩放图片
                right_preview.thumbnail((preview_width - 20, preview_height), Image.Resampling.LANCZOS)
                right_preview_x = separator_x + (cell_width // 2 - right_preview.width) // 2
                right_preview_y = y + 35
                img.paste(right_preview, (right_preview_x, right_preview_y))

                # 显示右侧文件名
                right_filename = new_img.name
                if len(right_filename) > 20:
                    right_filename = right_filename[:17] + "..."
                right_filename_bbox = draw.textbbox((0, 0), right_filename, font=small_font)
                right_filename_width = right_filename_bbox[2] - right_filename_bbox[0]
                right_filename_x = separator_x + (cell_width // 2 - right_filename_width) // 2
                draw.text((right_filename_x, y + 35 + preview_height + 10), right_filename,
                          fill=(127, 140, 141), font=small_font)

            except Exception as e:
                # 绘制占位符
                placeholder_size = 60
                placeholder_x = separator_x + (cell_width // 2 - placeholder_size) // 2
                placeholder_y = y + 35 + (preview_height - placeholder_size) // 2

                draw.rounded_rectangle(
                    [placeholder_x, placeholder_y, placeholder_x + placeholder_size, placeholder_y + placeholder_size],
                    radius=5,
                    fill=(225, 232, 237)
                )

                error_text = "加载失败"
                error_bbox = draw.textbbox((0, 0), error_text, font=small_font)
                error_width = error_bbox[2] - error_bbox[0]
                error_x = placeholder_x + (placeholder_size - error_width) // 2
                error_y = placeholder_y + (placeholder_size - small_font.size) // 2
                draw.text((error_x, error_y), error_text, fill=(127, 140, 141), font=small_font)

            # 绘制重复编号
            duplicate_number = f"重复 #{i + 1}"
            number_bbox = draw.textbbox((0, 0), duplicate_number, font=small_font)
            number_width = number_bbox[2] - number_bbox[0]
            number_x = x + (cell_width - number_width) // 2
            draw.text((number_x, y + cell_height - 20), duplicate_number,
                      fill=(149, 165, 166), font=small_font)

        # 转换为bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        print(f"生成重复报告图片失败: {e}")
        return None

async def find_all_duplicates() -> Dict[str, List[Tuple[Path, Path]]]:
    """
    查找所有文件夹中的重复图片（修复版本）
    """
    from .send import get_folder_display_info

    all_duplicates = {}
    folder_info_list = get_folder_display_info()

    for folder_info in folder_info_list:
        folder_name = folder_info["name"]
        duplicates = await find_folder_duplicates(folder_name)
        if duplicates:
            all_duplicates[folder_name] = duplicates

    return all_duplicates


async def find_folder_duplicates(folder_name: str) -> List[Tuple[Path, Path]]:
    """
    查找指定文件夹中的重复图片（修复版本）- 修复自身重复问题
    """
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return []

    folder_path = sticker_folders[actual_folder_name]

    # 获取文件夹中所有图片
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    image_files_set: Set[Path] = set()

    for ext in image_extensions:
        for file in folder_path.glob(f"*{ext}"):
            image_files_set.add(file)
        for file in folder_path.glob(f"*{ext.upper()}"):
            image_files_set.add(file)

    image_files = list(image_files_set)

    print(f"在文件夹 {folder_name} 中找到 {len(image_files)} 张图片")

    # 使用两种哈希方法进行重复检测
    perceptual_hashes = {}
    file_hashes = {}
    duplicates = []

    for img_path in image_files:
        # 计算感知哈希
        perceptual_hash = calculate_image_hash(img_path)
        # 计算文件哈希
        file_hash = calculate_image_hash_simple(img_path)

        if not perceptual_hash or not file_hash:
            continue

        # 检查感知哈希重复
        if perceptual_hash in perceptual_hashes:
            existing_img = perceptual_hashes[perceptual_hash]
            # 双重验证：文件哈希也必须相同
            if file_hash == file_hashes.get(existing_img, ""):
                # 关键修复：确保不是同一个文件
                if existing_img != img_path and await verify_duplicate(existing_img, img_path):
                    # 避免重复添加相同的重复对
                    duplicate_pair = (existing_img, img_path)
                    reverse_pair = (img_path, existing_img)
                    if duplicate_pair not in duplicates and reverse_pair not in duplicates:
                        duplicates.append(duplicate_pair)
                        print(f"发现重复: {existing_img.name} 和 {img_path.name}")
        else:
            perceptual_hashes[perceptual_hash] = img_path
            file_hashes[img_path] = file_hash

    print(f"在文件夹 {folder_name} 中发现 {len(duplicates)} 组重复图片")
    return duplicates


async def verify_duplicate(img1: Path, img2: Path) -> bool:
    """
    验证两张图片是否真的是重复（更严格的二次确认）
    """
    # 关键修复：确保不是同一个文件
    if img1 == img2:
        return False

    try:
        with Image.open(img1) as image1, Image.open(img2) as image2:
            # 转换为相同模式
            if image1.mode != 'RGB':
                image1 = image1.convert('RGB')
            if image2.mode != 'RGB':
                image2 = image2.convert('RGB')

            # 调整到相同尺寸
            image1 = image1.resize((100, 100), Image.Resampling.LANCZOS)
            image2 = image2.resize((100, 100), Image.Resampling.LANCZOS)

            if HAS_NUMPY:
                # 使用numpy计算像素差异
                arr1 = np.array(image1)
                arr2 = np.array(image2)

                # 计算均方误差
                mse = np.mean((arr1 - arr2) ** 2)

                # 提高阈值，减少误判
                return mse < 50  # 提高阈值
            else:
                # 纯Python实现
                pixels1 = list(image1.getdata())
                pixels2 = list(image2.getdata())

                if len(pixels1) != len(pixels2):
                    return False

                total_diff = 0
                for p1, p2 in zip(pixels1, pixels2):
                    if isinstance(p1, int):
                        # 灰度图
                        total_diff += abs(p1 - p2)
                    else:
                        # RGB图
                        total_diff += sum(abs(c1 - c2) for c1, c2 in zip(p1, p2))

                avg_diff = total_diff / len(pixels1)
                # 提高阈值
                return avg_diff < 100  # 提高阈值

    except Exception as e:
        print(f"验证重复图片失败 {img1} vs {img2}: {e}")
        return False


async def remove_duplicates(duplicates: Dict[str, List[Tuple[Path, Path]]]) -> int:
    """
    删除重复图片（安全版本）- 修复自身重复问题
    """
    removed_count = 0
    removed_files = []

    for folder_name, folder_duplicates in duplicates.items():
        print(f"处理文件夹 {folder_name} 的重复图片，共 {len(folder_duplicates)} 组")

        for existing_img, duplicate_img in folder_duplicates:
            try:
                # 安全验证：确保文件存在且不是同一个文件
                if (duplicate_img.exists() and
                        existing_img.exists() and
                        duplicate_img != existing_img):  # 确保不是同一个文件
                    print(f"准备删除重复图片: {duplicate_img.name}")
                    # 先移动到回收站或备份，而不是直接删除
                    backup_path = duplicate_img.with_suffix(duplicate_img.suffix + '.bak')
                    duplicate_img.rename(backup_path)
                    removed_files.append(backup_path)
                    removed_count += 1

            except Exception as e:
                print(f"删除重复图片失败 {duplicate_img}: {e}")

    # 记录删除操作
    if removed_files:
        log_file = sticker_dir / "duplicate_cleanup_log.txt"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"重复清理操作记录 - 删除了 {removed_count} 个文件:\n")
            for file_path in removed_files:
                f.write(f"{file_path}\n")

    return removed_count


async def safe_remove_duplicates(duplicates: Dict[str, List[Tuple[Path, Path]]]) -> Tuple[int, List[Path]]:
    """
    安全删除重复图片（推荐使用）- 修复自身重复问题
    """
    removed_count = 0
    removed_files = []

    for folder_name, folder_duplicates in duplicates.items():
        print(f"安全模式：处理文件夹 {folder_name} 的重复图片")

        for i, (existing_img, duplicate_img) in enumerate(folder_duplicates):
            try:
                # 多重安全检查
                if (duplicate_img.exists() and
                        existing_img.exists() and
                        duplicate_img != existing_img and  # 确保不是同一个文件
                        await verify_duplicate(existing_img, duplicate_img)):  # 再次验证

                    print(f"安全删除 [{i + 1}/{len(folder_duplicates)}]: {duplicate_img.name}")

                    # 创建备份而不是直接删除
                    backup_dir = sticker_dir / "backup_duplicates"
                    backup_dir.mkdir(exist_ok=True)
                    backup_path = backup_dir / duplicate_img.name

                    # 如果备份文件已存在，添加序号
                    counter = 1
                    while backup_path.exists():
                        backup_path = backup_dir / f"{duplicate_img.stem}_{counter}{duplicate_img.suffix}"
                        counter += 1

                    duplicate_img.rename(backup_path)
                    removed_files.append(backup_path)
                    removed_count += 1

            except Exception as e:
                print(f"安全删除失败 {duplicate_img}: {e}")

    return removed_count, removed_files


async def preview_duplicates_before_cleanup(all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> bytes:
    """
    在清理前预览将要删除的重复图片
    """
    try:
        if not all_duplicates:
            return None

        total_pairs = sum(len(duplicates) for duplicates in all_duplicates.values())

        # 计算布局
        padding = 30
        img_width = 600
        img_height = 400

        # 创建画布
        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        # 加载字体
        try:
            title_font = ImageFont.truetype("msyh.ttc", 32)
            text_font = ImageFont.truetype("msyh.ttc", 20)
            small_font = ImageFont.truetype("msyh.ttc", 16)
        except:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 32)
                text_font = ImageFont.truetype("simhei.ttf", 20)
                small_font = ImageFont.truetype("simhei.ttf", 16)
            except:
                title_font = ImageFont.load_default()
                text_font = ImageFont.load_default()
                small_font = ImageFont.load_default()

        # 绘制标题
        title = "重复图片清理预览"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img_width - title_width) // 2
        draw.text((title_x, padding), title, fill=(231, 76, 60), font=title_font)

        # 绘制警告信息
        warning_text = "警告：即将删除以下重复图片"
        warning_bbox = draw.textbbox((0, 0), warning_text, font=text_font)
        warning_width = warning_bbox[2] - warning_bbox[0]
        warning_x = (img_width - warning_width) // 2
        draw.text((warning_x, padding + 50), warning_text, fill=(231, 76, 60), font=text_font)

        # 绘制统计信息
        stats_text = f"检测到 {total_pairs} 组重复图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=text_font)
        stats_width = stats_bbox[2] - stats_bbox[0]
        stats_x = (img_width - stats_width) // 2
        draw.text((stats_x, padding + 90), stats_text, fill=(44, 62, 80), font=text_font)

        # 绘制各文件夹情况
        y_pos = padding + 140
        for folder_name, duplicates in all_duplicates.items():
            folder_text = f"{folder_name}: {len(duplicates)} 组重复"
            draw.text((padding, y_pos), folder_text, fill=(127, 140, 141), font=small_font)
            y_pos += 25

        # 绘制确认信息
        confirm_text = "请确认是否继续执行清理操作"
        confirm_bbox = draw.textbbox((0, 0), confirm_text, font=text_font)
        confirm_width = confirm_bbox[2] - confirm_bbox[0]
        confirm_x = (img_width - confirm_width) // 2
        confirm_y = img_height - padding - 40
        draw.text((confirm_x, confirm_y), confirm_text, fill=(231, 76, 60), font=text_font)

        # 转换为bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        print(f"生成清理预览图片失败: {e}")
        return None


async def render_cleanup_report(removed_count: int, all_duplicates: Dict[str, List[Tuple[Path, Path]]]) -> bytes:
    """
    渲染清理结果报告为图片
    """
    try:
        # 计算总重复组数
        total_duplicate_pairs = sum(len(duplicates) for duplicates in all_duplicates.values())

        # 计算布局
        padding = 30
        img_width = 600
        img_height = 400

        # 创建画布
        img = Image.new('RGB', (img_width, img_height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        # 加载字体
        try:
            title_font = ImageFont.truetype("msyh.ttc", 32)
            text_font = ImageFont.truetype("msyh.ttc", 20)
            small_font = ImageFont.truetype("msyh.ttc", 16)
        except:
            try:
                title_font = ImageFont.truetype("simhei.ttf", 32)
                text_font = ImageFont.truetype("simhei.ttf", 20)
                small_font = ImageFont.truetype("simhei.ttf", 16)
            except:
                title_font = ImageFont.load_default()
                text_font = ImageFont.load_default()
                small_font = ImageFont.load_default()

        # 绘制标题
        title = "重复图片清理报告"
        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img_width - title_width) // 2
        draw.text((title_x, padding), title, fill=(39, 174, 96), font=title_font)

        # 绘制统计信息
        stats_text = f"检测到 {total_duplicate_pairs} 组重复图片"
        stats_bbox = draw.textbbox((0, 0), stats_text, font=text_font)
        stats_width = stats_bbox[2] - stats_bbox[0]
        stats_x = (img_width - stats_width) // 2
        draw.text((stats_x, padding + 50), stats_text, fill=(44, 62, 80), font=text_font)

        result_text = f"已清理 {removed_count} 张重复图片"
        result_bbox = draw.textbbox((0, 0), result_text, font=text_font)
        result_width = result_bbox[2] - result_bbox[0]
        result_x = (img_width - result_width) // 2
        draw.text((result_x, padding + 90), result_text, fill=(39, 174, 96), font=text_font)

        # 绘制各文件夹重复情况
        y_pos = padding + 140
        for folder_name, duplicates in all_duplicates.items():
            folder_text = f"{folder_name}: {len(duplicates)} 组重复"
            draw.text((padding, y_pos), folder_text, fill=(127, 140, 141), font=small_font)
            y_pos += 25

        # 绘制底部说明
        footer_text = "重复图片已清理，每组合并保留一张图片"
        footer_bbox = draw.textbbox((0, 0), footer_text, font=small_font)
        footer_width = footer_bbox[2] - footer_bbox[0]
        footer_x = (img_width - footer_width) // 2
        footer_y = img_height - padding - 20
        draw.text((footer_x, footer_y), footer_text, fill=(127, 140, 141), font=small_font)

        # 转换为bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        print(f"生成清理报告图片失败: {e}")
        return None