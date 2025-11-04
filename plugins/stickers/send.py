# send.py
import random
import json
from pathlib import Path
from typing import Dict, Set, List

from nonebot_plugin_localstore import get_data_dir

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

                    # 创建文件夹路径
                    folder_path = sticker_dir / folder_name
                    folder_path.mkdir(exist_ok=True)

                    # 添加到主映射
                    sticker_folders[folder_name] = folder_path

                    # 添加别名映射
                    for alias in aliases:
                        alias_to_folder[alias] = folder_name

            print(f"从 list.json 加载了 {len(folder_configs)} 个贴图文件夹配置")
            print(f"可用文件夹: {list(sticker_folders.keys())}")
            print(f"别名映射: {alias_to_folder}")

        except Exception as e:
            print(f"加载 list.json 失败: {e}")
            # 如果加载失败，回退到扫描模式
            scan_sticker_folders_fallback()
    else:
        # 如果 list.json 不存在，创建默认文件
        create_default_list_json()
        print("list.json 不存在，已创建默认文件")


def scan_sticker_folders_fallback():
    """回退到扫描文件夹模式（兼容旧版本）"""
    global sticker_folders
    sticker_folders.clear()

    if sticker_dir.exists():
        for folder in sticker_dir.iterdir():
            if folder.is_dir() and folder.name != "__pycache__":
                sticker_folders[folder.name] = folder

    print(f"回退模式扫描完成，找到 {len(sticker_folders)} 个贴图文件夹: {list(sticker_folders.keys())}")


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
        print("已创建默认 list.json 文件")
    except Exception as e:
        print(f"创建默认 list.json 失败: {e}")


def resolve_folder_name(folder_name: str) -> str:
    """
    解析文件夹名称，支持别名

    返回: 实际文件夹名称
    """
    # 如果是别名，返回对应的实际文件夹名
    if folder_name in alias_to_folder:
        return alias_to_folder[folder_name]
    # 如果是实际文件夹名，直接返回
    elif folder_name in sticker_folders:
        return folder_name
    else:
        return folder_name


def get_random_sticker(folder_name: str) -> Path | None:
    """从指定文件夹中随机获取一张贴图（支持别名）"""
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return None

    folder = sticker_folders[actual_folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # 收集所有图片文件
    image_files = []
    for ext in image_extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    if not image_files:
        return None

    return random.choice(image_files)


def get_random_stickers(folder_name: str, count: int) -> List[Path]:
    """
    从指定文件夹中随机获取多张贴图（支持别名）

    返回: 图片路径列表
    """
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return []

    folder = sticker_folders[actual_folder_name]
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # 收集所有图片文件
    image_files = []
    for ext in image_extensions:
        image_files.extend(folder.glob(f"*{ext}"))
        image_files.extend(folder.glob(f"*{ext.upper()}"))

    if not image_files:
        return []

    # 如果请求数量超过可用图片数量，返回所有图片
    if count > len(image_files):
        return image_files

    # 随机选择不重复的图片
    return random.sample(image_files, count)


def count_images_in_folder(folder_name: str) -> int:
    """
    计算指定文件夹中的图片数量（支持别名）
    """
    # 解析实际文件夹名称
    actual_folder_name = resolve_folder_name(folder_name)

    if actual_folder_name not in sticker_folders:
        return 0

    folder = sticker_folders[actual_folder_name]
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


def get_folder_display_info() -> List[Dict]:
    """
    获取文件夹显示信息（用于统计等功能）

    返回: 包含文件夹名称、别名、图片数量的列表
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


# 初始化时加载配置
load_sticker_list()
