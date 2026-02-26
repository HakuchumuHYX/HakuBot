import json
import os
import uuid
from typing import List, Dict, Tuple
from nonebot import logger

from ..config import (
    get_group_text_path, get_group_image_dir, 
    get_group_image_list_path, DEFAULT_TEXTS
)

class TextDataManager:
    def __init__(self):
        self.group_texts: Dict[int, List[str]] = {}
        self.group_images: Dict[int, List[str]] = {}
        self.last_modified: Dict[int, float] = {}

    def load_text_data(self, group_id: int) -> bool:
        text_file_path = get_group_text_path(group_id)
        if not text_file_path.exists():
            self.group_texts[group_id] = []
            return self.save_text_data(group_id)
        try:
            current_modified = os.path.getmtime(text_file_path)
            if (group_id in self.last_modified and
                    current_modified == self.last_modified[group_id]):
                return True
            self.last_modified[group_id] = current_modified
            with open(text_file_path, 'r', encoding='utf-8') as f:
                file_content = f.read().strip()
                if not file_content:
                    self.group_texts[group_id] = []
                    return True
                loaded_data = json.loads(file_content)
            if isinstance(loaded_data, list):
                self.group_texts[group_id] = loaded_data
                return True
            else:
                logger.error(f"群 {group_id} 的文本文件格式错误，重置为空列表")
                self.group_texts[group_id] = []
                return self.save_text_data(group_id)
        except json.JSONDecodeError as e:
            logger.error(f"群 {group_id} 的文本文件JSON解析错误: {e}，重置为空列表")
            self.group_texts[group_id] = []
            return self.save_text_data(group_id)
        except Exception as e:
            logger.error(f"群 {group_id} 的文本文件加载失败: {e}")
            return False

    def load_image_data(self, group_id: int) -> bool:
        image_list_path = get_group_image_list_path(group_id)
        if not image_list_path.exists():
            self.group_images[group_id] = []
            return self.save_image_data(group_id)
        try:
            with open(image_list_path, 'r', encoding='utf-8') as f:
                file_content = f.read().strip()
                if not file_content:
                    self.group_images[group_id] = []
                    return True
                loaded_data = json.loads(file_content)
                if isinstance(loaded_data, list):
                    valid_images = []
                    image_dir = get_group_image_dir(group_id)
                    for filename in loaded_data:
                        if (image_dir / filename).exists():
                            valid_images.append(filename)
                        else:
                            logger.warning(f"群 {group_id} 的图片文件不存在: {filename}")
                    if len(valid_images) != len(loaded_data):
                        self.group_images[group_id] = valid_images
                        self.save_image_data(group_id)
                    else:
                        self.group_images[group_id] = loaded_data
                    return True
                else:
                    self.group_images[group_id] = []
                    return self.save_image_data(group_id)
        except Exception as e:
            logger.error(f"群 {group_id} 的图片列表加载失败: {e}")
            self.group_images[group_id] = []
            return False

    def save_text_data(self, group_id: int) -> bool:
        if group_id not in self.group_texts:
            self.group_texts[group_id] = []
        text_file_path = get_group_text_path(group_id)
        try:
            text_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(text_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.group_texts[group_id], f, ensure_ascii=False, indent=2)
            if os.path.exists(text_file_path):
                self.last_modified[group_id] = os.path.getmtime(text_file_path)
            return True
        except Exception as e:
            logger.error(f"保存群 {group_id} 的文本文件失败: {e}")
            return False

    def save_image_data(self, group_id: int) -> bool:
        if group_id not in self.group_images:
            self.group_images[group_id] = []
        image_list_path = get_group_image_list_path(group_id)
        try:
            image_list_path.parent.mkdir(parents=True, exist_ok=True)
            with open(image_list_path, 'w', encoding='utf-8') as f:
                json.dump(self.group_images[group_id], f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存群 {group_id} 的图片列表失败: {e}")
            return False

    def add_text(self, group_id: int, text: str) -> bool:
        if group_id not in self.group_texts:
            if not self.load_text_data(group_id):
                return False
        self.group_texts[group_id].append(text)
        return self.save_text_data(group_id)

    def add_image(self, group_id: int, image_data: bytes, file_extension: str) -> Tuple[bool, str]:
        if group_id not in self.group_images:
            if not self.load_image_data(group_id):
                return False, ""
        filename = f"{uuid.uuid4().hex}.{file_extension}"
        image_dir = get_group_image_dir(group_id)
        image_path = image_dir / filename
        try:
            with open(image_path, 'wb') as f:
                f.write(image_data)
            self.group_images[group_id].append(filename)
            if self.save_image_data(group_id):
                return True, filename
            else:
                if image_path.exists():
                    image_path.unlink()
                return False, ""
        except Exception as e:
            logger.error(f"保存图片文件失败: {e}")
            return False, ""

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
            self.group_images[group_id].remove(filename)
            self.save_image_data(group_id)
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
            self.group_images[group_id] = valid_images
            self.save_image_data(group_id)
            logger.info(f"群 {group_id} 清理了 {removed_count} 个不存在的图片文件")
        return removed_count

data_manager = TextDataManager()
