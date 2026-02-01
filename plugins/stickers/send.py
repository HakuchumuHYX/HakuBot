# stickers/send.py
"""
Stickers 插件 - 发送和文件管理模块
"""
import random
import json
import time
import threading
from pathlib import Path
from typing import Dict, Set, List, Optional

from nonebot_plugin_localstore import get_data_dir
from nonebot.log import logger

from .config import IMAGE_EXTENSIONS

# 插件数据目录
sticker_dir: Path = get_data_dir("stickers")
sticker_dir.mkdir(parents=True, exist_ok=True)

# list.json 文件路径
list_json_path: Path = sticker_dir / "list.json"

# 存储所有贴图文件夹的映射
sticker_folders: Dict[str, Path] = {}
# 存储别名映射
alias_to_folder: Dict[str, str] = {}
# 存储文件夹配置信息
folder_configs: List[Dict] = []

# 全局编号计数器
current_max_id: int = 0
_id_lock = threading.Lock()

# 图片计数缓存
_image_count_cache: Dict[str, int] = {}
_image_count_cache_time: Dict[str, float] = {}
_count_cache_lock = threading.Lock()
_COUNT_CACHE_TTL = 60  # 缓存有效期（秒）


# ==================== 公共函数 ====================

def get_all_images_in_folder(folder: Path) -> List[Path]:
    """
    获取文件夹中所有图片文件
    
    Args:
        folder: 文件夹路径
        
    Returns:
        图片文件路径列表（去重）
    """
    if not folder.exists():
        return []
    
    image_files: Set[Path] = set()
    
    for ext in IMAGE_EXTENSIONS:
        # 匹配小写扩展名
        image_files.update(folder.glob(f"*{ext}"))
        # 匹配大写扩展名
        image_files.update(folder.glob(f"*{ext.upper()}"))
    
    return list(image_files)


def get_all_images_across_folders() -> List[Path]:
    """
    获取所有文件夹中的所有图片
    
    Returns:
        所有图片文件路径列表
    """
    all_images: List[Path] = []
    
    for folder_path in sticker_folders.values():
        all_images.extend(get_all_images_in_folder(folder_path))
    
    return all_images


# ==================== 编号计数器 ====================

def refresh_max_id() -> None:
    """
    遍历所有已知文件夹，找到当前最大的纯数字编号
    线程安全版本
    """
    global current_max_id
    
    logger.info("正在初始化全局图片编号计数器...")

    try:
        max_id = 0
        
        for folder_path in sticker_folders.values():
            if not folder_path.exists():
                continue

            try:
                for file in folder_path.iterdir():
                    if file.is_file():
                        stem = file.stem
                        if stem.isdigit():
                            try:
                                num = int(stem)
                                if num > max_id:
                                    max_id = num
                            except ValueError:
                                pass
            except PermissionError as e:
                logger.warning(f"无法访问文件夹 {folder_path}: {e}")
                continue

        # 持锁更新全局变量
        with _id_lock:
            if max_id > current_max_id:
                current_max_id = max_id

        logger.info(f"编号初始化完成，当前最大编号: {current_max_id}")

    except Exception as e:
        logger.error(f"初始化编号计数器失败: {e}")


def get_next_image_id() -> int:
    """
    获取下一个图片编号（线程安全）
    
    Returns:
        下一个可用的编号
    """
    global current_max_id
    with _id_lock:
        current_max_id += 1
        return current_max_id


# ==================== 配置加载 ====================

def load_sticker_list() -> None:
    """从 list.json 加载贴图文件夹配置"""
    global sticker_folders, alias_to_folder, folder_configs

    sticker_folders.clear()
    alias_to_folder.clear()
    folder_configs = []

    if list_json_path.exists():
        try:
            with open(list_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                folder_configs = data.get("folders", [])

                for folder_config in folder_configs:
                    folder_name = folder_config["name"]
                    aliases = folder_config.get("aliases", [])

                    folder_path = sticker_dir / folder_name
                    folder_path.mkdir(exist_ok=True)

                    sticker_folders[folder_name] = folder_path

                    for alias in aliases:
                        alias_to_folder[alias] = folder_name

            logger.info(f"从 list.json 加载了 {len(folder_configs)} 个贴图文件夹配置")
            logger.debug(f"可用文件夹: {list(sticker_folders.keys())}")
            logger.debug(f"别名映射: {alias_to_folder}")

        except Exception as e:
            logger.error(f"加载 list.json 失败: {e}")
            scan_sticker_folders_fallback()
    else:
        create_default_list_json()
        logger.warning("list.json 不存在，已创建默认文件")

    refresh_max_id()


def scan_sticker_folders_fallback() -> None:
    """回退到扫描文件夹模式（兼容旧版本）"""
    global sticker_folders
    sticker_folders.clear()

    if sticker_dir.exists():
        for folder in sticker_dir.iterdir():
            if folder.is_dir() and folder.name != "__pycache__":
                sticker_folders[folder.name] = folder

    logger.warning(f"回退模式扫描完成，找到 {len(sticker_folders)} 个贴图文件夹: {list(sticker_folders.keys())}")


def create_default_list_json() -> None:
    """创建默认的 list.json 文件"""
    default_data = {
        "folders": [
            {
                "name": "example",
                "aliases": ["demo", "示例"]
            }
        ]
    }

    try:
        with open(list_json_path, 'w', encoding='utf-8') as f:
            json.dump(default_data, f, ensure_ascii=False, indent=2)
        logger.success("已创建默认 list.json 文件")
    except Exception as e:
        logger.error(f"创建默认 list.json 失败: {e}")


# ==================== 文件夹解析 ====================

def resolve_folder_name(folder_name: str) -> str:
    """
    解析文件夹名称（支持别名）
    
    Args:
        folder_name: 文件夹名称或别名
        
    Returns:
        实际的文件夹名称
    """
    if folder_name in alias_to_folder:
        return alias_to_folder[folder_name]
    elif folder_name in sticker_folders:
        return folder_name
    else:
        return folder_name


# ==================== 随机获取 ====================

def get_random_sticker(folder_name: str) -> Optional[Path]:
    """
    从指定文件夹随机获取一张贴图
    
    Args:
        folder_name: 文件夹名称（支持 "stickers" 表示所有文件夹）
        
    Returns:
        贴图文件路径，或 None
    """
    if folder_name.lower() == "stickers":
        # 从所有文件夹中随机选择
        all_images = get_all_images_across_folders()
        if not all_images:
            return None
        return random.choice(all_images)
    
    actual_folder_name = resolve_folder_name(folder_name)
    
    if actual_folder_name not in sticker_folders:
        return None

    folder = sticker_folders[actual_folder_name]
    image_files = get_all_images_in_folder(folder)

    if not image_files:
        return None

    return random.choice(image_files)


def get_random_stickers(folder_name: str, count: int) -> List[Path]:
    """
    从指定文件夹随机获取多张贴图（不重复）
    
    Args:
        folder_name: 文件夹名称（支持 "stickers" 表示所有文件夹）
        count: 获取数量
        
    Returns:
        贴图文件路径列表
    """
    if folder_name.lower() == "stickers":
        # 从所有文件夹中随机选择
        all_images = get_all_images_across_folders()
    else:
        actual_folder_name = resolve_folder_name(folder_name)
        
        if actual_folder_name not in sticker_folders:
            return []
        
        folder = sticker_folders[actual_folder_name]
        all_images = get_all_images_in_folder(folder)

    if not all_images:
        return []

    # 使用 random.sample 一次性获取，更高效
    actual_count = min(count, len(all_images))
    return random.sample(all_images, actual_count)


# ==================== 统计函数 ====================

def count_images_in_folder(folder_name: str, use_cache: bool = True) -> int:
    """
    统计指定文件夹中的图片数量（带缓存）
    
    Args:
        folder_name: 文件夹名称
        use_cache: 是否使用缓存（默认启用）
        
    Returns:
        图片数量
    """
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return 0

    # 检查缓存
    if use_cache:
        current_time = time.time()
        with _count_cache_lock:
            if actual_folder_name in _image_count_cache:
                cache_time = _image_count_cache_time.get(actual_folder_name, 0)
                if current_time - cache_time < _COUNT_CACHE_TTL:
                    return _image_count_cache[actual_folder_name]

    # 缓存未命中或过期，重新计算
    folder = sticker_folders[actual_folder_name]
    count = len(get_all_images_in_folder(folder))
    
    # 更新缓存
    with _count_cache_lock:
        _image_count_cache[actual_folder_name] = count
        _image_count_cache_time[actual_folder_name] = time.time()
    
    return count


def invalidate_count_cache(folder_name: str = None) -> None:
    """
    使图片计数缓存失效
    
    Args:
        folder_name: 指定文件夹名称，None 则清空所有缓存
    """
    with _count_cache_lock:
        if folder_name is None:
            _image_count_cache.clear()
            _image_count_cache_time.clear()
        else:
            actual_folder_name = resolve_folder_name(folder_name)
            _image_count_cache.pop(actual_folder_name, None)
            _image_count_cache_time.pop(actual_folder_name, None)


def get_folder_display_info() -> List[Dict]:
    """
    获取所有文件夹的显示信息
    
    Returns:
        文件夹信息列表，包含 name, aliases, image_count
    """
    result = []
    for folder_config in folder_configs:
        folder_name = folder_config["name"]
        aliases = folder_config.get("aliases", [])
        image_count = count_images_in_folder(folder_name)

        result.append({
            "name": folder_name,
            "aliases": aliases,
            "image_count": image_count
        })

    return result
