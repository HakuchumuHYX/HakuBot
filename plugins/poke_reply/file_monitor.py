# poke_reply/file_monitor.py
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from nonebot import logger

from .config import TEXT_FILES_DIR, IMAGE_FILES_DIR, get_group_image_dir
from .models.data import data_manager

class JsonFileHandler(FileSystemEventHandler):
    """监听JSON文件变化的处理器"""

    def __init__(self, on_modified_callback=None):
        self.on_modified_callback = on_modified_callback

    def on_modified(self, event):
        if event.src_path.endswith('.json'):
            # 从文件名提取群号
            filename = os.path.basename(event.src_path)
            if filename.startswith('text_') and filename.endswith('.json'):
                try:
                    group_id = int(filename[5:-5])  # 提取数字部分
                    # 清除该群组的相似度检查缓存
                    from .services.text import similarity_checker
                    similarity_checker.clear_group_cache(group_id)

                    if self.on_modified_callback:
                        self.on_modified_callback(group_id)
                    else:
                        # 默认行为：重新加载数据
                        data_manager.load_text_data(group_id)
                except ValueError:
                    pass  # 文件名格式不正确，忽略
            elif filename.startswith('images_') and filename.endswith('.json'):
                try:
                    group_id = int(filename[7:-5])  # 提取数字部分，去掉"images_"前缀
                    # 重新加载图片数据
                    data_manager.load_image_data(group_id)
                except ValueError:
                    pass  # 文件名格式不正确，忽略

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

            # 监听文本文件目录
            if TEXT_FILES_DIR.exists():
                self.observer.schedule(event_handler, path=str(TEXT_FILES_DIR), recursive=False)
            
            # 监听图片列表文件目录
            if IMAGE_FILES_DIR.exists():
                self.observer.schedule(event_handler, path=str(IMAGE_FILES_DIR), recursive=False)

            # 监听所有群组的图片目录（用于检测图片文件删除）
            # 注意：如果目录太多，这可能会有问题，但对于现有逻辑，我们暂时保持
            for group_id in data_manager.get_all_group_ids():
                image_dir = get_group_image_dir(group_id)
                if image_dir.exists():
                    self.observer.schedule(event_handler, path=str(image_dir), recursive=False)

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
