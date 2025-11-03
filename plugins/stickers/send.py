import random
import asyncio
from pathlib import Path
from typing import Dict, List, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from nonebot_plugin_localstore import get_data_dir

# 插件数据
sticker_dir = get_data_dir("stickers")
sticker_dir.mkdir(parents=True, exist_ok=True)

# 存储所有贴图文件夹的映射
sticker_folders: Dict[str, Path] = {}

# 文件夹监视器相关变量
observer = None
is_watching = False


class StickerFolderHandler(FileSystemEventHandler):
    """处理文件夹变化的事件处理器"""

    def on_created(self, event):
        """当有新的文件夹或文件创建时"""
        if event.is_directory:
            # 新文件夹创建
            folder_name = Path(event.src_path).name
            if folder_name not in sticker_folders:
                sticker_folders[folder_name] = Path(event.src_path)
                print(f"检测到新贴图文件夹: {folder_name}")
        else:
            # 新文件创建，重新扫描对应文件夹
            parent_folder = Path(event.src_path).parent
            if parent_folder in sticker_folders.values():
                # 文件在已知贴图文件夹中，无需特别处理
                pass

    def on_deleted(self, event):
        """当有文件夹或文件被删除时"""
        if event.is_directory:
            # 文件夹被删除
            folder_name = Path(event.src_path).name
            if folder_name in sticker_folders:
                del sticker_folders[folder_name]
                print(f"检测到贴图文件夹被删除: {folder_name}")
        else:
            # 文件被删除，无需特别处理
            pass

    def on_moved(self, event):
        """当有文件夹或文件被移动时"""
        if event.is_directory:
            # 文件夹被移动
            old_folder_name = Path(event.src_path).name
            new_folder_name = Path(event.dest_path).name

            if old_folder_name in sticker_folders:
                # 更新映射
                sticker_folders[new_folder_name] = Path(event.dest_path)
                del sticker_folders[old_folder_name]
                print(f"检测到贴图文件夹重命名: {old_folder_name} -> {new_folder_name}")
        else:
            # 文件被移动，无需特别处理
            pass


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


async def start_folder_watcher():
    """启动文件夹监视器"""
    global observer, is_watching

    if is_watching:
        return

    try:
        event_handler = StickerFolderHandler()
        observer = Observer()
        observer.schedule(event_handler, str(sticker_dir), recursive=True)
        observer.start()
        is_watching = True
        print("贴图文件夹监视器已启动")
    except Exception as e:
        print(f"启动文件夹监视器失败: {e}")


async def stop_folder_watcher():
    """停止文件夹监视器"""
    global observer, is_watching

    if observer and is_watching:
        observer.stop()
        observer.join()
        is_watching = False
        print("贴图文件夹监视器已停止")


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