# stickers/send.py
import random
import json
import threading
from pathlib import Path
from typing import Dict, Set, List

from nonebot_plugin_localstore import get_data_dir
from nonebot.log import logger

# 插件数据
sticker_dir = get_data_dir("stickers")
sticker_dir.mkdir(parents=True, exist_ok=True)

# list.json 文件路径
list_json_path = sticker_dir / "list.json"

# 存储所有贴图文件夹的映射
sticker_folders: Dict[str, Path] = {}
# 存储别名映射
alias_to_folder: Dict[str, str] = {}
# 存储文件夹配置信息
folder_configs: List[Dict] = []

# 全局编号计数器
current_max_id: int = 0
_id_lock = threading.Lock()


def refresh_max_id():
    """
    遍历所有已知文件夹，找到当前最大的纯数字编号
    """
    global current_max_id
    max_id = 0
    logger.info("正在初始化全局图片编号计数器...")

    try:
        for folder_path in sticker_folders.values():
            if not folder_path.exists():
                continue

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

        with _id_lock:
            current_max_id = max_id

        logger.info(f"编号初始化完成，当前最大编号: {current_max_id}")

    except Exception as e:
        logger.error(f"初始化编号计数器失败: {e}")


def get_next_image_id() -> int:
    global current_max_id
    with _id_lock:
        current_max_id += 1
        return current_max_id


def load_sticker_list():
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

            # <--- 修改日志
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


def scan_sticker_folders_fallback():
    """回退到扫描文件夹模式（兼容旧版本）"""
    global sticker_folders
    sticker_folders.clear()

    if sticker_dir.exists():
        for folder in sticker_dir.iterdir():
            if folder.is_dir() and folder.name != "__pycache__":
                sticker_folders[folder.name] = folder

    logger.warning(f"回退模式扫描完成，找到 {len(sticker_folders)} 个贴图文件夹: {list(sticker_folders.keys())}")


def create_default_list_json():
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


def resolve_folder_name(folder_name: str) -> str:
    if folder_name in alias_to_folder:
        return alias_to_folder[folder_name]
    elif folder_name in sticker_folders:
        return folder_name
    else:
        return folder_name


def get_random_sticker(folder_name: str) -> Path | None:
    if folder_name.lower() == "stickers":
        if not sticker_folders:
            return None
        folder_names = list(sticker_folders.keys())
        if not folder_names:
            return None
        actual_folder_name = random.choice(folder_names)
    else:
        actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return None

    folder = sticker_folders[actual_folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    image_files = []
    for ext in image_extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    if not image_files:
        return None

    return random.choice(image_files)


def get_random_stickers(folder_name: str, count: int) -> List[Path]:
    if folder_name.lower() == "stickers":
        if not sticker_folders:
            return []

        all_folder_names = list(sticker_folders.keys())
        if not all_folder_names:
            return []

        selected_images: List[Path] = []
        max_attempts = count * 5

        while len(selected_images) < count and max_attempts > 0:
            max_attempts -= 1
            image_path = get_random_sticker("stickers")
            if image_path:
                if image_path not in selected_images:
                    selected_images.append(image_path)

        return selected_images

    else:
        actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return []

    folder = sticker_folders[actual_folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    image_files: Set[Path] = set()

    for ext in image_extensions:
        for file in folder.glob(f"*{ext}"):
            image_files.add(file)
        for file in folder.glob(f"*{ext.upper()}"):
            image_files.add(file)

    image_files_list = list(image_files)

    if not image_files_list:
        return []

    if count > len(image_files_list):
        return image_files_list

    return random.sample(image_files_list, count)


def count_images_in_folder(folder_name: str) -> int:
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return 0

    folder = sticker_folders[actual_folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    image_files: Set[Path] = set()

    for ext in image_extensions:
        for file in folder.glob(f"*{ext}"):
            image_files.add(file)
        for file in folder.glob(f"*{ext.upper()}"):
            image_files.add(file)

    return len(image_files)


def get_folder_display_info() -> List[Dict]:
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
