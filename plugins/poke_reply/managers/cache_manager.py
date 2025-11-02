# managers/cache_manager.py
import json
import time
from pathlib import Path
from typing import Dict, Optional
from nonebot import logger

from ..config import data_dir
from ..utils.common import get_group_id

# 文件路径
MESSAGE_CACHE_FILE = data_dir / "message_cache.json"

# 缓存过期时间（10分钟）
CACHE_EXPIRE_TIME = 10 * 60


class MessageCache:
    def __init__(self):
        self.cache_file = MESSAGE_CACHE_FILE
        self.cache_data: Dict[str, dict] = {}
        self.load_cache()

    def load_cache(self):
        """加载消息缓存数据"""
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
        """保存消息缓存数据"""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存消息缓存失败: {e}")

    def add_message(self, group_id: int, message_id: int, content: str,
                    message_type: str = "text", image_hash: str = ""):
        """
        添加消息到缓存

        Args:
            group_id: 群组ID
            message_id: 消息ID
            content: 消息内容
            message_type: 消息类型 (text, image, text_image)
            image_hash: 图片哈希（仅对图片消息有效）
        """
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
        logger.info(f"已缓存消息: 群组={group_id}, 消息ID={message_id}, 类型={message_type}, 内容长度={len(content)}")

    def get_message(self, group_id: int, message_id: int) -> Optional[dict]:
        """获取消息缓存"""
        cache_key = f"{group_id}_{message_id}"
        self.clean_expired_cache()
        return self.cache_data.get(cache_key)

    def remove_message(self, group_id: int, message_id: int) -> bool:
        """移除消息缓存"""
        cache_key = f"{group_id}_{message_id}"
        if cache_key in self.cache_data:
            del self.cache_data[cache_key]
            self.save_cache()
            logger.info(f"已移除消息缓存: 群组={group_id}, 消息ID={message_id}")
            return True
        return False

    def clean_expired_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = []

        for key, record in self.cache_data.items():
            if record.get("expire_time", 0) < current_time:
                expired_keys.append(key)

        for key in expired_keys:
            del self.cache_data[key]

        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 条过期消息缓存")
            self.save_cache()

    def get_message_by_image_hash(self, image_hash: str) -> Optional[dict]:
        """通过图片哈希查找消息"""
        self.clean_expired_cache()
        for record in self.cache_data.values():
            if record.get("image_hash") == image_hash:
                return record
        return None


# 全局实例
message_cache = MessageCache()