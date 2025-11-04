# send.py
import random
from pathlib import Path
from typing import Dict, Set

from nonebot_plugin_localstore import get_data_dir

# 插件数据
sticker_dir = get_data_dir("stickers")
sticker_dir.mkdir(parents=True, exist_ok=True)

# 存储所有贴图文件夹的映射
sticker_folders: Dict[str, Path] = {}


def scan_sticker_folders():
    """扫描所有贴图文件夹"""
    global sticker_folders
    sticker_folders.clear()

    if sticker_dir.exists():
        for folder in sticker_dir.iterdir():
            if folder.is_dir():
                sticker_folders[folder.name] = folder

    print(f"扫描完成，找到 {len(sticker_folders)} 个贴图文件夹: {list(sticker_folders.keys())}")


def get_random_sticker(folder_name: str) -> Path | None:
    """从指定文件夹中随机获取一张贴图"""
    if folder_name not in sticker_folders:
        return None

    folder = sticker_folders[folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # 收集所有图片文件
    image_files = []
    for ext in image_extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    if not image_files:
        return None

    return random.choice(image_files)


def count_images_in_folder(folder_name: str) -> int:
    """
    计算指定文件夹中的图片数量
    """
    if folder_name not in sticker_folders:
        return 0

    folder = sticker_folders[folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # 使用集合来避免重复计数
    image_files: Set[Path] = set()

    for ext in image_extensions:
        # 使用小写扩展名
        for file in folder.glob(f"*{ext}"):
            image_files.add(file)
        # 使用大写扩展名
        for file in folder.glob(f"*{ext.upper()}"):
            image_files.add(file)

    return len(image_files)