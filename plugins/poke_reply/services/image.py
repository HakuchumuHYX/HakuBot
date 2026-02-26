import hashlib
import io
import json
import time
from pathlib import Path
from typing import Tuple, Dict, Optional, List, Set, Union
from PIL import Image
from nonebot import logger

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("Numpy 未安装，将使用纯Python模式进行图片比较")

from ..config import (
    IMAGE_HASH_CACHE_FILE, CACHE_VERSION, IMAGE_HASH_CACHE_TTL,
    IMAGE_SIMILARITY_THRESHOLD, get_group_image_dir
)
from ..models.data import data_manager

# --- 缓存管理 ---
_hash_cache = None

def load_hash_cache() -> Dict:
    global _hash_cache
    if _hash_cache is not None:
        return _hash_cache

    if not IMAGE_HASH_CACHE_FILE.exists():
        _hash_cache = {"version": CACHE_VERSION, "entries": {}}
        return _hash_cache

    try:
        with open(IMAGE_HASH_CACHE_FILE, 'r', encoding='utf-8') as f:
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
    global _hash_cache
    if _hash_cache is None:
        return
    try:
        with open(IMAGE_HASH_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_hash_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存图片哈希缓存失败: {e}")

def get_cache_key(image_path: Path) -> str:
    try:
        stat = image_path.stat()
        return f"{image_path.absolute()}:{stat.st_size}:{stat.st_mtime}"
    except:
        return str(image_path.absolute())

def get_cached_hash(image_path: Path, hash_type: str) -> Tuple[str, bool]:
    if not image_path.exists():
        return "", False
    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)
    entry = cache["entries"].get(cache_key)
    if not entry:
        return "", False
    if time.time() - entry.get("timestamp", 0) > IMAGE_HASH_CACHE_TTL:
        if cache_key in cache["entries"]:
            del cache["entries"][cache_key]
        return "", False
    return entry.get(hash_type, ""), True

def update_hash_cache(image_path: Path, perceptual_hash: str, file_hash: str):
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
    cache = load_hash_cache()
    cache_key = get_cache_key(image_path)
    if cache_key in cache["entries"]:
        del cache["entries"][cache_key]
        save_hash_cache()
        logger.info(f"已使 {image_path.name} 的哈希缓存失效")

# --- 哈希计算 ---

def calculate_perceptual_hash(img: Image.Image) -> str:
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img = img.resize((64, 64), Image.Resampling.LANCZOS)
    img = img.convert('L')
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    hash_str = ''.join('1' if pixel > avg else '0' for pixel in pixels)
    return hashlib.md5(hash_str.encode()).hexdigest()

def calculate_file_hash(image_bytes: bytes) -> str:
    return hashlib.md5(image_bytes).hexdigest()

def get_hashes_from_path(image_path: Path) -> Tuple[Optional[str], Optional[str]]:
    p_hash, from_cache_p = get_cached_hash(image_path, "perceptual_hash")
    f_hash, from_cache_f = get_cached_hash(image_path, "file_hash")
    if from_cache_p and from_cache_f:
        return p_hash, f_hash
    try:
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        file_hash = hashlib.md5(image_bytes).hexdigest()
        with Image.open(io.BytesIO(image_bytes)) as img:
            perceptual_hash = calculate_perceptual_hash(img)
        update_hash_cache(image_path, perceptual_hash, file_hash)
        return perceptual_hash, file_hash
    except Exception as e:
        logger.error(f"计算哈希失败 {image_path}: {e}")
        return None, None

def get_hashes_from_bytes(image_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    try:
        file_hash = calculate_file_hash(image_bytes)
        with Image.open(io.BytesIO(image_bytes)) as img:
            perceptual_hash = calculate_perceptual_hash(img)
        return perceptual_hash, file_hash
    except Exception as e:
        logger.error(f"从bytes计算哈希失败: {e}")
        return None, None

# --- 验证逻辑 ---

async def _verify_duplicate_check(img1: Image.Image, img2: Image.Image) -> bool:
    try:
        if img1.mode != 'RGB': img1 = img1.convert('RGB')
        if img2.mode != 'RGB': img2 = img2.convert('RGB')
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
            if len(pixels1) != len(pixels2): return False
            total_diff = sum(sum(abs(c1 - c2) for c1, c2 in zip(p1, p2)) for p1, p2 in zip(pixels1, pixels2))
            avg_diff = total_diff / len(pixels1) / 3
            return avg_diff < IMAGE_SIMILARITY_THRESHOLD * 2
    except Exception as e:
        logger.error(f"验证重复图片时出错: {e}")
        return False

async def verify_duplicate_bytes_vs_path(img_path_1: Path, img_bytes_2: bytes) -> bool:
    if not img_path_1.exists(): return False
    try:
        with Image.open(img_path_1) as image1, Image.open(io.BytesIO(img_bytes_2)) as image2:
            return await _verify_duplicate_check(image1, image2)
    except Exception as e:
        logger.error(f"验证(Path vs Bytes)失败: {e}")
        return False

async def verify_duplicate_path_vs_path(img_path_1: Path, img_path_2: Path) -> bool:
    if not img_path_1.exists() or not img_path_2.exists(): return False
    if img_path_1.absolute() == img_path_2.absolute(): return False
    try:
        with Image.open(img_path_1) as image1, Image.open(img_path_2) as image2:
            return await _verify_duplicate_check(image1, image2)
    except Exception as e:
        logger.error(f"验证(Path vs Path)失败: {e}")
        return False

# --- 对外接口 ---

async def check_duplicate_image(group_id: int, new_image_bytes: bytes) -> Tuple[bool, Optional[str]]:
    """(投稿用) 检查新图片是否与群组中现有图片重复"""
    new_p_hash, new_f_hash = get_hashes_from_bytes(new_image_bytes)
    if not new_p_hash or not new_f_hash: return False, None
    if not data_manager.ensure_group_data_loaded(group_id): return False, None

    existing_images = data_manager.group_images.get(group_id, [])
    image_dir = get_group_image_dir(group_id)
    existing_perceptual_hashes: Dict[str, Path] = {}

    for filename in existing_images:
        img_path = image_dir / filename
        if not img_path.exists(): continue
        p_hash, f_hash = get_hashes_from_path(img_path)
        if p_hash:
            if p_hash not in existing_perceptual_hashes:
                existing_perceptual_hashes[p_hash] = img_path
            if f_hash == new_f_hash:
                logger.info(f"发现重复 (文件哈希): {img_path.name}")
                return True, filename

    if new_p_hash in existing_perceptual_hashes:
        existing_img_path = existing_perceptual_hashes[new_p_hash]
        if await verify_duplicate_bytes_vs_path(existing_img_path, new_image_bytes):
            logger.info("二次验证通过，确认为重复")
            return True, existing_img_path.name
            
    return False, None

async def find_group_duplicates(group_id: int) -> List[Tuple[Path, Path]]:
    """(SU清理用) 查找重复图片"""
    if not data_manager.ensure_group_data_loaded(group_id): return []
    image_files = data_manager.group_images.get(group_id, [])
    image_dir = get_group_image_dir(group_id)
    perceptual_hashes: Dict[str, Path] = {}
    file_hashes: Dict[Path, str] = {}
    duplicates_to_remove: List[Tuple[Path, Path]] = []

    for filename in image_files:
        img_path = image_dir / filename
        if not img_path.exists(): continue
        p_hash, f_hash = get_hashes_from_path(img_path)
        if not p_hash or not f_hash: continue

        if p_hash in perceptual_hashes:
            existing_img = perceptual_hashes[p_hash]
            existing_f_hash = file_hashes.get(existing_img)
            if (f_hash == existing_f_hash and
                    await verify_duplicate_path_vs_path(existing_img, img_path)):
                duplicates_to_remove.append((existing_img, img_path))
        else:
            perceptual_hashes[p_hash] = img_path
            file_hashes[img_path] = f_hash
    return duplicates_to_remove

def safe_remove_group_duplicates(group_id: int, duplicates: List[Tuple[Path, Path]]) -> int:
    """(SU清理用) 删除重复图片"""
    image_list = data_manager.group_images.get(group_id)
    if image_list is None: return 0
    
    files_to_remove_names: Set[str] = {remove_path.name for (_, remove_path) in duplicates}
    if not files_to_remove_names: return 0
    
    new_image_list = [f for f in image_list if f not in files_to_remove_names]
    removed_count = len(image_list) - len(new_image_list)
    
    data_manager.group_images[group_id] = new_image_list
    if not data_manager.save_image_data(group_id):
        data_manager.group_images[group_id] = image_list
        return 0
        
    for (_, remove_path) in duplicates:
        try:
            if remove_path.exists():
                remove_path.unlink()
                invalidate_cache_for_file(remove_path)
        except Exception:
            pass
    return removed_count
