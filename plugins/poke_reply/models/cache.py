import json
import time
from typing import Dict, Optional
from nonebot import logger
from ..config import (
    MESSAGE_CACHE_FILE, TEXT_IMAGE_CACHE_FILE, CACHE_EXPIRE_TIME
)

class MessageCache:
    def __init__(self):
        self.cache_file = MESSAGE_CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache_data = json.load(f)
                logger.info(f"消息缓存加载成功，共 {len(self.cache_data)} 条记录")
            else:
                self.cache_data = {}
                self.save_cache()
        except Exception as e:
            logger.error(f"加载消息缓存失败: {e}")
            self.cache_data = {}

    def save_cache(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存消息缓存失败: {e}")

    def add_message(self, group_id: int, message_id: int, content: str,
                    message_type: str = "text", image_hash: str = ""):
        cache_key = f"{group_id}_{message_id}"
        self.cache_data[cache_key] = {
            "group_id": group_id,
            "message_id": message_id,
            "content": content,
            "type": message_type,
            "image_hash": image_hash,
            "timestamp": time.time(),
            "expire_time": time.time() + CACHE_EXPIRE_TIME
        }
        self.save_cache()
        logger.debug(f"已缓存消息: 群组={group_id}, 消息ID={message_id}, 类型={message_type}")

    def get_message(self, group_id: int, message_id: int) -> Optional[dict]:
        cache_key = f"{group_id}_{message_id}"
        self.clean_expired_cache()
        return self.cache_data.get(cache_key)

    def remove_message(self, group_id: int, message_id: int) -> bool:
        cache_key = f"{group_id}_{message_id}"
        if cache_key in self.cache_data:
            del self.cache_data[cache_key]
            self.save_cache()
            return True
        return False

    def clean_expired_cache(self):
        current_time = time.time()
        expired_keys = [key for key, record in self.cache_data.items() if record.get("expire_time", 0) < current_time]
        for key in expired_keys:
            del self.cache_data[key]
        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 条过期消息缓存")
            self.save_cache()


class TextImageCache:
    def __init__(self):
        self.cache_file = TEXT_IMAGE_CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache_data = json.load(f)
            else:
                self.cache_data = {}
        except Exception as e:
            logger.error(f"加载文本图片缓存失败: {e}")
            self.cache_data = {}

    def save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存文本图片缓存失败: {e}")

    def add_cache_by_image_hash(self, image_hash: str, group_id: int, original_text: str):
        cache_key = f"{group_id}_{image_hash}"
        self.cache_data[cache_key] = {
            "image_hash": image_hash,
            "group_id": group_id,
            "original_text": original_text,
            "expire_time": time.time() + CACHE_EXPIRE_TIME
        }
        self.save_cache()

    def get_cache_by_image_hash(self, image_hash: str, group_id: int) -> Optional[dict]:
        cache_key = f"{group_id}_{image_hash}"
        self.clean_expired_cache()
        return self.cache_data.get(cache_key)

    def remove_cache_by_image_hash(self, image_hash: str, group_id: int) -> bool:
        cache_key = f"{group_id}_{image_hash}"
        if cache_key in self.cache_data:
            del self.cache_data[cache_key]
            self.save_cache()
            return True
        return False

    def clean_expired_cache(self):
        current_time = time.time()
        expired_keys = [key for key, record in self.cache_data.items() if record.get("expire_time", 0) < current_time]
        for key in expired_keys:
            del self.cache_data[key]
        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 条过期文本图片缓存")
            self.save_cache()

# 全局实例
message_cache = MessageCache()
text_image_cache = TextImageCache()
