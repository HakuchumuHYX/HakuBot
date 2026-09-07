import time
from typing import Dict, Optional
from nonebot import logger
from plugins.poke_reply.config import (
    MESSAGE_CACHE_FILE,
    TEXT_IMAGE_CACHE_FILE,
    CACHE_EXPIRE_TIME,
)
from utils.json_io import atomic_write_json, load_json


def _load_cache(path, label: str) -> dict:
    try:
        result = load_json(
            path, expected_type=dict, missing_ok=True, backup_on_error=True, default={}
        )
        if not result.success:
            logger.error(f"加载{label}缓存失败: {result.error}")
        return result.data
    except Exception as error:
        logger.error(f"加载{label}缓存失败: {error}")
        return {}


def _save_cache(path, data: dict, label: str) -> None:
    try:
        atomic_write_json(path, data, expected_type=dict)
    except Exception as error:
        logger.error(f"保存{label}缓存失败: {error}")


def _clean_expired_cache(data: dict, now: Optional[float] = None) -> int:
    current_time = now if now is not None else time.time()
    expired_keys = [
        key for key, record in data.items()
        if record.get("expire_time", 0) < current_time
    ]
    for key in expired_keys:
        del data[key]
    return len(expired_keys)


class MessageCache:
    def __init__(self):
        self.cache_file = MESSAGE_CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        self.cache_data = _load_cache(self.cache_file, "消息")
        if not self.cache_file.exists():
            self.save_cache()

    def save_cache(self):
        _save_cache(self.cache_file, self.cache_data, "消息")

    def add_message(
        self,
        group_id: int,
        message_id: int,
        content: str,
        message_type: str = "text",
        image_hash: str = "",
    ):
        cache_key = f"{group_id}_{message_id}"
        self.cache_data[cache_key] = {
            "group_id": group_id,
            "message_id": message_id,
            "content": content,
            "type": message_type,
            "image_hash": image_hash,
            "timestamp": time.time(),
            "expire_time": time.time() + CACHE_EXPIRE_TIME,
        }
        self.save_cache()
        logger.debug(
            f"已缓存消息: 群组={group_id}, 消息ID={message_id}, 类型={message_type}"
        )

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

    def clean_expired_cache(self, now: Optional[float] = None) -> int:
        removed = _clean_expired_cache(self.cache_data, now)
        if removed:
            logger.info(f"清理了 {removed} 条过期消息缓存")
            self.save_cache()
        return removed


class TextImageCache:
    def __init__(self):
        self.cache_file = TEXT_IMAGE_CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        self.cache_data = _load_cache(self.cache_file, "文本图片")

    def save_cache(self):
        _save_cache(self.cache_file, self.cache_data, "文本图片")

    def add_cache_by_image_hash(
        self, image_hash: str, group_id: int, original_text: str
    ):
        cache_key = f"{group_id}_{image_hash}"
        self.cache_data[cache_key] = {
            "image_hash": image_hash,
            "group_id": group_id,
            "original_text": original_text,
            "expire_time": time.time() + CACHE_EXPIRE_TIME,
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

    def clean_expired_cache(self, now: Optional[float] = None) -> int:
        removed = _clean_expired_cache(self.cache_data, now)
        if removed:
            logger.info(f"清理了 {removed} 条过期文本图片缓存")
            self.save_cache()
        return removed


# 全局实例
message_cache = MessageCache()
text_image_cache = TextImageCache()
