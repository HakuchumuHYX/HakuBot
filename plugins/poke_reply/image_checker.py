# poke_reply/image_checker.py
# (包含上一步的查重功能 + 本次新增的SU清理功能)
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Tuple, Dict, Optional, List, Set
from PIL import Image

# 导入 poke_reply 的配置和数据管理器
from .config import data_dir, IMAGE_SIMILARITY_THRESHOLD
from .data_manager import data_manager
from .config import get_group_image_dir
from nonebot import logger  # 导入 logger

# 尝试导入 numpy
try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("Numpy 未安装，将使用纯Python模式进行图片比较")

# 缓存文件路径 (使用 poke_reply 的 data_dir)
CACHE_FILE = data_dir / "image_hash_cache.json"
CACHE_VERSION = "1.0_poke_reply"
CACHE_TTL = 30 * 24 * 60 * 60  # 30天

# 全局缓存字典
_hash_cache = None


# --- 缓存管理 (逻辑基本来自 stickers/check.py) ---

def load_hash_cache() -> Dict:
    """加载哈希缓存"""
    global _hash_cache
    if _hash_cache is not None:
        return _hash_cache

    if not CACHE_FILE.exists():
        _hash_cache = {"version": CACHE_VERSION, "entries": {}}
        return _hash_cache

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        if cache_data.get("version") != CACHE_VERSION:
            _hash_cache = {"version": CACHE_VERSION, "entries": {}}
        else:
            _hash_cache = cache_data
    except Exception as e:
        logger.error(f"加载图片哈希缓存失败: {e}，创建新缓存")
        _hash_cache = {"version": CACHE_VERSION, "entries": {}}
    return _hash_cache


def save_hash_cache():
    """保存哈希缓存到文件"""
    global _hash_cache
    if _hash_cache is None:
        return
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_hash_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存图片哈希缓存失败: {e}")


def get_cache_key(image_path: Path) -> str:
    """生成缓存键：文件路径 + 文件大小 + 修改时间"""
    try:
        stat = image_path.stat()
        return f"{image_path.absolute()}:{stat.st_size}:{stat.st_mtime}"
    except:
        return str(image_path.absolute())


def get_cached_hash(image_path: Path, hash_type: str) -> Tuple[str, bool]:
    """从缓存获取哈希值"""
    if not image_path.exists():
        return "", False
    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)
    entry = cache["entries"].get(cache_key)
    if not entry:
        return "", False
    # 检查缓存是否过期
    if time.time() - entry.get("timestamp", 0) > CACHE_TTL:
        if cache_key in cache["entries"]:
            del cache["entries"][cache_key]
        return "", False
    return entry.get(hash_type, ""), True


def update_hash_cache(image_path: Path, perceptual_hash: str, file_hash: str):
    """更新缓存中的哈希值"""
    if not image_path.exists():
        return
    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)
    cache["entries"][cache_key] = {
        "perceptual_hash": perceptual_hash,
        "file_hash": file_hash,
        "timestamp": time.time(),
    }
    save_hash_cache()


def invalidate_cache_for_file(image_path: Path):
    """使指定文件的缓存失效（例如删除时）"""
    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)
    if cache_key in cache["entries"]:
        del cache["entries"][cache_key]
        save_hash_cache()
        logger.info(f"已使 {image_path.name} 的哈希缓存失效")


# --- 哈希计算 (逻辑来自 stickers/check.py) ---

def calculate_perceptual_hash(img: Image.Image) -> str:
    """计算感知哈希 (p-hash)"""
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img = img.resize((64, 64), Image.Resampling.LANCZOS)
    img = img.convert('L')
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    hash_str = ''.join('1' if pixel > avg else '0' for pixel in pixels)
    return hashlib.md5(hash_str.encode()).hexdigest()


def calculate_file_hash(image_bytes: bytes) -> str:
    """计算文件哈希 (md5)"""
    return hashlib.md5(image_bytes).hexdigest()


def get_hashes_from_path(image_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """从文件路径获取哈希（带缓存）"""
    # 1. 尝试从缓存获取
    p_hash, from_cache_p = get_cached_hash(image_path, "perceptual_hash")
    f_hash, from_cache_f = get_cached_hash(image_path, "file_hash")

    if from_cache_p and from_cache_f:
        return p_hash, f_hash

    # 2. 缓存未命中，计算
    try:
        with open(image_path, 'rb') as f:
            image_bytes = f.read()

        file_hash = hashlib.md5(image_bytes).hexdigest()

        with Image.open(io.BytesIO(image_bytes)) as img:
            perceptual_hash = calculate_perceptual_hash(img)

        # 更新缓存
        update_hash_cache(image_path, perceptual_hash, file_hash)
        return perceptual_hash, file_hash

    except Exception as e:
        logger.error(f"计算哈希失败 {image_path}: {e}")
        return None, None


def get_hashes_from_bytes(image_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """从 bytes 计算哈希值"""
    try:
        file_hash = calculate_file_hash(image_bytes)
        with Image.open(io.BytesIO(image_bytes)) as img:
            perceptual_hash = calculate_perceptual_hash(img)
        return perceptual_hash, file_hash
    except Exception as e:
        logger.error(f"从bytes计算哈希失败: {e}")
        return None, None


# --- 验证 (逻辑来自 stickers/check.py) ---

async def _verify_duplicate_check(img1: Image.Image, img2: Image.Image) -> bool:
    """内部验证函数，用于比较两个已打开的PIL Image对象"""
    try:
        if img1.mode != 'RGB':
            img1 = img1.convert('RGB')
        if img2.mode != 'RGB':
            img2 = img2.convert('RGB')

        image1 = img1.resize((100, 100), Image.Resampling.LANCZOS)
        image2 = img2.resize((100, 100), Image.Resampling.LANCZOS)

        if HAS_NUMPY:
            arr1 = np.array(image1)
            arr2 = np.array(image2)
            mse = np.mean((arr1 - arr2) ** 2)
            return mse < IMAGE_SIMILARITY_THRESHOLD
        else:
            pixels1 = list(image1.getdata())
            pixels2 = list(image2.getdata())
            if len(pixels1) != len(pixels2):
                return False

            total_diff = 0
            for p1, p2 in zip(pixels1, pixels2):
                total_diff += sum(abs(c1 - c2) for c1, c2 in zip(p1, p2))

            avg_diff = total_diff / len(pixels1) / 3  # (除以3个通道)
            pil_threshold = IMAGE_SIMILARITY_THRESHOLD * 2
            return avg_diff < pil_threshold
    except Exception as e:
        logger.error(f"验证重复图片时出错: {e}")
        return False


async def verify_duplicate_bytes_vs_path(img_path_1: Path, img_bytes_2: bytes) -> bool:
    """(投稿用) 验证：文件 vs bytes"""
    if not img_path_1.exists():
        return False
    try:
        with Image.open(img_path_1) as image1, Image.open(io.BytesIO(img_bytes_2)) as image2:
            return await _verify_duplicate_check(image1, image2)
    except Exception as e:
        logger.error(f"验证(Path vs Bytes)失败: {e}")
        return False


async def verify_duplicate_path_vs_path(img_path_1: Path, img_path_2: Path) -> bool:
    """(SU清理用) 验证：文件 vs 文件"""
    if not img_path_1.exists() or not img_path_2.exists():
        return False
    # 避免比较自身
    if img_path_1.absolute() == img_path_2.absolute():
        return False
    try:
        with Image.open(img_path_1) as image1, Image.open(img_path_2) as image2:
            return await _verify_duplicate_check(image1, image2)
    except Exception as e:
        logger.error(f"验证(Path vs Path)失败: {e}")
        return False


# --- 投稿查重 (上一步已实现) ---

async def check_duplicate_image(group_id: int, new_image_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """(投稿用) 检查新图片是否与群组中现有图片重复"""

    new_p_hash, new_f_hash = get_hashes_from_bytes(new_image_bytes)
    if not new_p_hash or not new_f_hash:
        logger.warning("无法计算新图片的哈希，跳过查重")
        return False, None

    if not data_manager.ensure_group_data_loaded(group_id):
        logger.warning(f"群 {group_id} 数据未加载，跳过查重")
        return False, None

    existing_images = data_manager.group_images.get(group_id, [])
    if not existing_images:
        return False, None

    existing_perceptual_hashes: Dict[str, Path] = {}
    image_dir = get_group_image_dir(group_id)

    for filename in existing_images:
        img_path = image_dir / filename
        if not img_path.exists():
            continue

        p_hash, f_hash = get_hashes_from_path(img_path)

        if p_hash:
            # 存储感知哈希
            if p_hash not in existing_perceptual_hashes:
                existing_perceptual_hashes[p_hash] = img_path

            # 1. 检查文件哈希 (MD5)
            if f_hash == new_f_hash:
                logger.info(f"发现重复 (文件哈希): {img_path.name}")
                return True, filename

    # 2. 检查感知哈希
    if new_p_hash in existing_perceptual_hashes:
        existing_img_path = existing_perceptual_hashes[new_p_hash]
        logger.info(f"发现潜在重复 (感知哈希): {existing_img_path.name}，进行二次验证...")

        # 3. 双重验证
        if await verify_duplicate_bytes_vs_path(existing_img_path, new_image_bytes):
            logger.info("二次验证通过，确认为重复")
            return True, existing_img_path.name
        else:
            logger.info("二次验证未通过，非重复图片")

    return False, None


# --- vvvvvv 【新增：SU 清理重复功能】 vvvvvv ---

async def find_group_duplicates(group_id: int) -> List[Tuple[Path, Path]]:
    """
    (SU清理用) 查找指定群组中的所有重复图片
    返回: (保留的图片, 待删除的图片) 列表
    """
    if not data_manager.ensure_group_data_loaded(group_id):
        logger.error(f"群 {group_id} 数据加载失败，无法查找重复")
        return []

    image_files = data_manager.group_images.get(group_id, [])
    image_dir = get_group_image_dir(group_id)

    perceptual_hashes: Dict[str, Path] = {}
    file_hashes: Dict[Path, str] = {}
    duplicates_to_remove: List[Tuple[Path, Path]] = []  # (keep, remove)

    logger.info(f"开始在群 {group_id} 中查找重复图片 (共 {len(image_files)} 张)...")

    for filename in image_files:
        img_path = image_dir / filename
        if not img_path.exists():
            continue

        p_hash, f_hash = get_hashes_from_path(img_path)
        if not p_hash or not f_hash:
            logger.warning(f"无法计算哈希: {filename}")
            continue

        # 检查感知哈希
        if p_hash in perceptual_hashes:
            existing_img = perceptual_hashes[p_hash]
            existing_f_hash = file_hashes.get(existing_img)

            # 双重验证：文件哈希 和 严格验证
            if (f_hash == existing_f_hash and
                    await verify_duplicate_path_vs_path(existing_img, img_path)):
                logger.info(f"发现重复: 保留 {existing_img.name}, 移除 {img_path.name}")
                duplicates_to_remove.append((existing_img, img_path))
        else:
            # 这是此哈希值第一次出现，记录它
            perceptual_hashes[p_hash] = img_path
            file_hashes[img_path] = f_hash

    logger.info(f"群 {group_id} 查重完毕，发现 {len(duplicates_to_remove)} 组重复。")
    return duplicates_to_remove


def safe_remove_group_duplicates(group_id: int, duplicates: List[Tuple[Path, Path]]) -> int:
    """
    (SU清理用) 安全删除重复的图片（文件和数据）
    """
    image_list = data_manager.group_images.get(group_id)
    if image_list is None:
        logger.error("无法获取群组图片列表，停止删除")
        return 0

    # 1. 确定所有要删除的文件名
    files_to_remove_names: Set[str] = {remove_path.name for (keep_path, remove_path) in duplicates}

    if not files_to_remove_names:
        return 0

    # 2. 从 data_manager 中移除
    initial_count = len(image_list)
    new_image_list = [filename for filename in image_list if filename not in files_to_remove_names]
    removed_count = initial_count - len(new_image_list)

    data_manager.group_images[group_id] = new_image_list
    if not data_manager.save_image_data(group_id):
        logger.error(f"群 {group_id} 图片列表保存失败！停止删除文件。")
        # 回滚
        data_manager.group_images[group_id] = image_list
        return 0

    # 3. 从文件系统删除文件并清理缓存
    for (keep_path, remove_path) in duplicates:
        try:
            if remove_path.exists():
                remove_path.unlink()
                logger.info(f"已删除文件: {remove_path.name}")
                # 4. 清理哈希缓存
                invalidate_cache_for_file(remove_path)
            else:
                logger.warning(f"文件 {remove_path.name} 已不存在，跳过删除")
        except Exception as e:
            logger.error(f"删除文件 {remove_path.name} 失败: {e}")

    logger.info(f"群 {group_id} 成功清理了 {removed_count} 张重复图片。")
    return removed_count

# --- ^^^^^^ 【新增：SU 清理重复功能】 ^^^^^^ ---