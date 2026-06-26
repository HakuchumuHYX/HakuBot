# poke_reply/file_monitor.py
import os
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from nonebot import logger

from .config import TEXT_FILES_DIR, IMAGE_FILES_DIR, get_group_image_dir
from .models.data import data_manager

DEBOUNCE_SECONDS = 0.2


def discover_watch_paths():
    watch_paths = []
    if TEXT_FILES_DIR.exists():
        watch_paths.append(TEXT_FILES_DIR)
    if IMAGE_FILES_DIR.exists():
        watch_paths.append(IMAGE_FILES_DIR)
        for child in IMAGE_FILES_DIR.iterdir():
            if not child.is_dir() or not child.name.startswith("group_"):
                continue
            try:
                int(child.name[len("group_"):])
            except ValueError:
                continue
            watch_paths.append(child)
    return watch_paths

class JsonFileHandler(FileSystemEventHandler):
    """监听JSON文件变化的处理器"""

    def __init__(self, on_modified_callback=None):
        self.on_modified_callback = on_modified_callback
        self._last_event_time = {}

    def on_modified(self, event):
        self._handle_json_path(getattr(event, "src_path", ""), getattr(event, "is_directory", False))

    def on_created(self, event):
        self._handle_json_path(getattr(event, "src_path", ""), getattr(event, "is_directory", False))

    def on_moved(self, event):
        self._handle_json_path(getattr(event, "dest_path", ""), getattr(event, "is_directory", False))

    def _handle_json_path(self, file_path, is_directory=False):
        if is_directory or not file_path or not file_path.endswith('.json'):
            return
        path = Path(file_path)
        if self._should_ignore_json_path(path):
            return
        if self._is_debounced(path):
            return

        filename = path.name
        if filename.startswith('text_') and filename.endswith('.json'):
            try:
                group_id = int(filename[5:-5])
            except ValueError:
                return

            from .services.text import similarity_checker
            similarity_checker.clear_group_cache(group_id)

            if self.on_modified_callback:
                self.on_modified_callback(group_id)
            else:
                data_manager.load_text_data(group_id)
        elif filename.startswith('images_') and filename.endswith('.json'):
            try:
                group_id = int(filename[7:-5])
            except ValueError:
                return
            data_manager.load_image_data(group_id)

    def _should_ignore_json_path(self, path: Path) -> bool:
        filename = path.name
        if filename.startswith("."):
            return True
        ignored_markers = (".corrupt.", ".recovered.", ".before_cache_recovery.")
        return any(marker in filename for marker in ignored_markers)

    def _is_debounced(self, path: Path) -> bool:
        now = time.monotonic()
        key = str(path)
        last_time = self._last_event_time.get(key)
        self._last_event_time[key] = now
        return last_time is not None and now - last_time < DEBOUNCE_SECONDS

    def on_deleted(self, event):
        """处理文件删除事件"""
        # 检查是否是图片文件被删除
        file_path = event.src_path
        if not file_path.endswith('.json') and not os.path.isdir(file_path):
            # 尝试从路径中提取群号和文件名
            for group_id in data_manager.get_all_group_ids():
                image_dir = get_group_image_dir(group_id)
                if image_dir.exists() and str(image_dir) in file_path:
                    # 这是某个群组的图片目录下的文件
                    filename = os.path.basename(file_path)
                    # 从图片列表中移除这个文件名
                    if group_id in data_manager.group_images and filename in data_manager.group_images[group_id]:
                        data_manager.group_images[group_id].remove(filename)
                        data_manager.save_image_data(group_id)
                        logger.info(f"检测到图片文件 {filename} 被删除，已从群 {group_id} 的图片列表中移除")
                        break


class FileMonitor:
    """文件监听器"""

    def __init__(self):
        self.observer = None
        self.is_monitoring = False

    def start_monitoring(self, on_modified_callback=None):
        """启动文件监听"""
        try:
            self.observer = Observer()
            event_handler = JsonFileHandler(on_modified_callback)

            for watch_path in discover_watch_paths():
                self.observer.schedule(event_handler, path=str(watch_path), recursive=False)

            self.observer.start()
            self.is_monitoring = True
            logger.info("文件监听器已启动")
            return True
        except Exception as e:
            logger.error(f"启动文件监听器失败: {e}")
            return False

    def stop_monitoring(self):
        """停止文件监听"""
        try:
            if self.observer and self.is_monitoring:
                self.observer.stop()
                self.observer.join()
                self.is_monitoring = False
                logger.info("文件监听器已停止")
                return True
        except Exception as e:
            logger.error(f"停止文件监听器失败: {e}")
        return False

    def is_alive(self):
        """检查监听器是否在运行"""
        return self.observer and self.observer.is_alive()


# 全局文件监听器实例
file_monitor = FileMonitor()
