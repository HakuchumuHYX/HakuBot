import json
import os
import shutil
import threading
import time
import uuid
from typing import List, Dict, Tuple
from nonebot import logger

from ..config import (
    get_group_text_path, get_group_image_dir,
    get_group_image_list_path, DEFAULT_TEXTS
)
from ..utils.json_store import atomic_write_json, load_json_file

class TextDataManager:
    def __init__(self):
        self.group_texts: Dict[int, List[str]] = {}
        self.group_images: Dict[int, List[str]] = {}
        self.last_modified: Dict[int, float] = {}
        self._lock = threading.RLock()
        self._corrupt_backup_keys = set()

    def load_text_data(self, group_id: int) -> bool:
        with self._lock:
            text_file_path = get_group_text_path(group_id)
            if not text_file_path.exists():
                self.group_texts[group_id] = []
                return self.save_text_data(group_id)
            try:
                current_modified = os.path.getmtime(text_file_path)
                if (group_id in self.last_modified and
                        current_modified == self.last_modified[group_id] and
                        group_id in self.group_texts):
                    return True
                with open(text_file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                if not file_content.strip():
                    logger.error(f"群 {group_id} 的文本文件为空，保留当前内存数据且不覆盖文件")
                    return False
                loaded_data = json.loads(file_content)
                if isinstance(loaded_data, list):
                    self.group_texts[group_id] = loaded_data
                    self.last_modified[group_id] = current_modified
                    return True
                logger.error(f"群 {group_id} 的文本文件格式错误，保留当前内存数据且不覆盖文件")
                self._backup_corrupt_text_file(text_file_path, group_id)
                return False
            except json.JSONDecodeError as e:
                logger.error(f"群 {group_id} 的文本文件JSON解析错误: {e}，保留当前内存数据且不覆盖文件")
                self._backup_corrupt_text_file(text_file_path, group_id)
                return False
            except Exception as e:
                logger.error(f"群 {group_id} 的文本文件加载失败: {e}")
                return False

    def _backup_corrupt_text_file(self, text_file_path, group_id: int) -> None:
        try:
            file_stat = text_file_path.stat()
            backup_key = f"{text_file_path}:{file_stat.st_mtime_ns}:{file_stat.st_size}"
            if backup_key in self._corrupt_backup_keys:
                return
            backup_path = text_file_path.with_name(
                f"{text_file_path.name}.corrupt.{int(time.time())}"
            )
            shutil.copy2(text_file_path, backup_path)
            self._corrupt_backup_keys.add(backup_key)
            logger.warning(f"群 {group_id} 的异常文本文件已备份到: {backup_path}")
        except Exception as e:
            logger.error(f"备份群 {group_id} 的异常文本文件失败: {e}")

    def load_image_data(self, group_id: int) -> bool:
        with self._lock:
            image_list_path = get_group_image_list_path(group_id)
            if not image_list_path.exists():
                self.group_images[group_id] = []
                return self.save_image_data(group_id)

            result = load_json_file(image_list_path, list, default=[])
            if not result.success:
                logger.error(f"群 {group_id} 的图片列表加载失败，保留当前内存数据且不覆盖文件: {result.error}")
                return False

            loaded_data = result.data
            valid_images = []
            image_dir = get_group_image_dir(group_id)
            for filename in loaded_data:
                if (image_dir / filename).exists():
                    valid_images.append(filename)
                else:
                    logger.warning(f"群 {group_id} 的图片文件不存在: {filename}")
            if len(valid_images) != len(loaded_data):
                self.group_images[group_id] = valid_images
                return self.save_image_data(group_id)
            self.group_images[group_id] = loaded_data
            return True

    def save_text_data(self, group_id: int) -> bool:
        with self._lock:
            if group_id not in self.group_texts:
                self.group_texts[group_id] = []
            text_file_path = get_group_text_path(group_id)
            if atomic_write_json(text_file_path, self.group_texts[group_id], list):
                if os.path.exists(text_file_path):
                    self.last_modified[group_id] = os.path.getmtime(text_file_path)
                return True
            logger.error(f"保存群 {group_id} 的文本文件失败")
            return False

    def save_image_data(self, group_id: int) -> bool:
        with self._lock:
            if group_id not in self.group_images:
                self.group_images[group_id] = []
            image_list_path = get_group_image_list_path(group_id)
            if atomic_write_json(image_list_path, self.group_images[group_id], list):
                return True
            logger.error(f"保存群 {group_id} 的图片列表失败")
            return False

    def add_text(self, group_id: int, text: str) -> bool:
        with self._lock:
            if group_id not in self.group_texts:
                if not self.load_text_data(group_id):
                    return False
            old_texts = list(self.group_texts[group_id])
            self.group_texts[group_id] = old_texts + [text]
            if self.save_text_data(group_id):
                return True
            self.group_texts[group_id] = old_texts
            return False

    def remove_text(self, group_id: int, text: str) -> bool:
        with self._lock:
            if group_id not in self.group_texts:
                if not self.load_text_data(group_id):
                    return False
            if text not in self.group_texts[group_id]:
                return False
            old_texts = list(self.group_texts[group_id])
            new_texts = list(old_texts)
            new_texts.remove(text)
            self.group_texts[group_id] = new_texts
            if self.save_text_data(group_id):
                return True
            self.group_texts[group_id] = old_texts
            return False

    def add_image(self, group_id: int, image_data: bytes, file_extension: str) -> Tuple[bool, str]:
        with self._lock:
            if group_id not in self.group_images:
                if not self.load_image_data(group_id):
                    return False, ""
            filename = f"{uuid.uuid4().hex}.{file_extension}"
            image_dir = get_group_image_dir(group_id)
            image_path = image_dir / filename
            old_images = list(self.group_images[group_id])
            try:
                with open(image_path, 'wb') as f:
                    f.write(image_data)
                self.group_images[group_id] = old_images + [filename]
                if self.save_image_data(group_id):
                    return True, filename
                self.group_images[group_id] = old_images
                if image_path.exists():
                    image_path.unlink()
                return False, ""
            except Exception as e:
                self.group_images[group_id] = old_images
                try:
                    if image_path.exists():
                        image_path.unlink()
                except Exception as cleanup_error:
                    logger.error(f"清理保存失败的图片文件失败: {cleanup_error}")
                logger.error(f"保存图片文件失败: {e}")
                return False, ""

    def remove_image(self, group_id: int, filename: str) -> bool:
        with self._lock:
            if group_id not in self.group_images:
                if not self.load_image_data(group_id):
                    return False
            if filename not in self.group_images[group_id]:
                return False

            old_images = list(self.group_images[group_id])
            new_images = [name for name in old_images if name != filename]
            self.group_images[group_id] = new_images
            if not self.save_image_data(group_id):
                self.group_images[group_id] = old_images
                return False

            image_path = get_group_image_dir(group_id) / filename
            try:
                if image_path.exists():
                    image_path.unlink()
            except Exception as e:
                logger.error(f"删除图片文件失败 {image_path}: {e}")
            return True

    def get_random_text(self, group_id: int) -> str:
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return "数据加载失败，请联系管理员喵！"
        if (not self.group_texts[group_id] or
                not self.is_text_list_valid(group_id)):
            return "这个群还没有投稿内容喵，快来投稿吧！"
        import random
        return random.choice(self.group_texts[group_id])

    def get_random_image_path(self, group_id: int) -> str:
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return ""
        if not self.group_images[group_id]:
            return ""
        import random
        image_dir = get_group_image_dir(group_id)
        filename = random.choice(self.group_images[group_id])
        image_path = image_dir / filename
        if image_path.exists():
            return str(image_path)
        else:
            self.remove_image(group_id, filename)
            return ""

    def get_content_weights(self, group_id: int) -> Tuple[int, int]:
        text_count = self.get_text_count(group_id)
        image_count = self.get_image_count(group_id)
        return text_count, image_count

    def get_text_count(self, group_id: int) -> int:
        if group_id not in self.group_texts:
            return 0
        return len(self.group_texts[group_id])

    def get_image_count(self, group_id: int) -> int:
        if group_id not in self.group_images:
            return 0
        return len(self.group_images[group_id])

    def is_text_list_valid(self, group_id: int) -> bool:
        return (group_id in self.group_texts and
                isinstance(self.group_texts[group_id], list) and
                (not self.group_texts[group_id] or
                 self.group_texts[group_id][0] not in DEFAULT_TEXTS))

    def ensure_group_data_loaded(self, group_id: int) -> bool:
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return False
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return False
        return True
    
    def get_all_group_ids(self) -> List[int]:
        text_group_ids = set(self.group_texts.keys())
        image_group_ids = set(self.group_images.keys())
        return list(text_group_ids.union(image_group_ids))

    def cleanup_missing_images(self, group_id: int) -> int:
        if group_id not in self.group_images:
            return 0
        initial_count = len(self.group_images[group_id])
        image_dir = get_group_image_dir(group_id)
        valid_images = [filename for filename in self.group_images[group_id] if (image_dir / filename).exists()]
        removed_count = initial_count - len(valid_images)
        if removed_count > 0:
            old_images = list(self.group_images[group_id])
            self.group_images[group_id] = valid_images
            if self.save_image_data(group_id):
                logger.info(f"群 {group_id} 清理了 {removed_count} 个不存在的图片文件")
            else:
                self.group_images[group_id] = old_images
                return 0
        return removed_count

data_manager = TextDataManager()
