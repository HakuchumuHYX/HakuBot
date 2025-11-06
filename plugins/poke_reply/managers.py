# poke_reply/managers.py
import json
import time
import hashlib
import asyncio
from pathlib import Path
from typing import Dict, Optional, List
from nonebot import logger, get_driver

# vvvvvv 【修改：导入路径】 vvvvvv
from .config import data_dir
from .common import get_group_id

# --- 缓存管理器 (来自 cache_manager.py) ---
MESSAGE_CACHE_FILE = data_dir / "message_cache.json"
CACHE_EXPIRE_TIME = 10 * 60  # 10分钟


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
        logger.info(f"已缓存消息: 群组={group_id}, 消息ID={message_id}, 类型={message_type}")

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


# --- 文本图片缓存 (来自 text_image_cache.py) ---
TEXT_IMAGE_CACHE_FILE = data_dir / "text_image_cache.json"


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


# --- 删除申请管理器 (来自 delete_request_manager.py) ---
DELETE_REQUESTS_FILE = data_dir / "delete_requests.json"


class DeleteRequestManager:
    def __init__(self):
        self.requests_file = DELETE_REQUESTS_FILE
        self.requests_data: Dict[str, dict] = {}
        self.load_requests()

    def load_requests(self):
        try:
            if self.requests_file.exists():
                with open(self.requests_file, 'r', encoding='utf-8') as f:
                    self.requests_data = json.load(f)
            else:
                self.requests_data = {}
        except Exception as e:
            logger.error(f"加载删除申请失败: {e}")
            self.requests_data = {}

    def save_requests(self):
        try:
            with open(self.requests_file, 'w', encoding='utf-8') as f:
                json.dump(self.requests_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存删除申请失败: {e}")

    def add_request(self, group_id: int, message_id: int, requester_id: int,
                    content: str, message_type: str) -> str:
        request_id = hashlib.md5(f"{group_id}_{message_id}_{time.time()}".encode()).hexdigest()[:8]
        self.requests_data[request_id] = {
            "request_id": request_id,
            "group_id": group_id,
            "message_id": message_id,
            "requester_id": requester_id,
            "content": content[:200] + "..." if len(content) > 200 else content,
            "type": message_type,
            "status": "pending",
            "request_time": time.time(),
            "process_time": None,
            "processor_id": None
        }
        self.save_requests()
        return request_id

    def get_pending_requests(self) -> List[dict]:
        return [req for req in self.requests_data.values() if req["status"] == "pending"]

    def get_request(self, request_id: str) -> Optional[dict]:
        return self.requests_data.get(request_id)

    def update_request(self, request_id: str, status: str, processor_id: int) -> bool:
        if request_id in self.requests_data:
            self.requests_data[request_id].update({
                "status": status,
                "process_time": time.time(),
                "processor_id": processor_id
            })
            self.save_requests()
            return True
        return False

    def remove_request(self, request_id: str) -> bool:
        if request_id in self.requests_data:
            del self.requests_data[request_id]
            self.save_requests()
            return True
        return False

    def clean_expired_requests(self):
        current_time = time.time()
        expired_keys = []
        for key, record in self.requests_data.items():
            if (record["status"] != "pending" and
                    current_time - record.get("process_time", 0) > 86400):  # 24小时
                expired_keys.append(key)
        for key in expired_keys:
            del self.requests_data[key]
        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 个过期的删除申请")


# --- 全局实例 ---
message_cache = MessageCache()
text_image_cache = TextImageCache()
delete_request_manager = DeleteRequestManager()


# --- 定时清理任务 (来自 management.py) ---
async def start_cache_cleaner():
    async def clean_expired_cache():
        while True:
            await asyncio.sleep(300)  # 每5分钟清理一次
            message_cache.clean_expired_cache()
            text_image_cache.clean_expired_cache()
            delete_request_manager.clean_expired_requests()
            logger.debug("已执行定时缓存清理")

    asyncio.create_task(clean_expired_cache())


@get_driver().on_startup
async def init_management():
    await start_cache_cleaner()
    logger.info("poke_reply 管理模块（缓存）初始化完成")