import hashlib
import time
from typing import Dict, List, Optional
from nonebot import logger
from ..config import DELETE_REQUESTS_FILE
from ..utils.json_store import atomic_write_json, load_json_file

class DeleteRequestManager:
    def __init__(self):
        self.requests_file = DELETE_REQUESTS_FILE
        self.requests_data: Dict[str, dict] = {}
        # 映射 通知消息ID -> 申请ID
        self.notification_map: Dict[int, str] = {}
        self.load_requests()

    def add_notification_map(self, message_id: int, request_id: str):
        """记录通知消息ID与申请ID的关联"""
        self.notification_map[message_id] = request_id

    def get_request_id_by_notification(self, message_id: int) -> Optional[str]:
        """通过通知消息ID获取申请ID"""
        return self.notification_map.get(message_id)

    def load_requests(self):
        try:
            if self.requests_file.exists():
                result = load_json_file(self.requests_file, dict, default={})
                self.requests_data = result.data
                if not result.success:
                    logger.error(f"加载删除申请失败: {result.error}")
            else:
                self.requests_data = {}
        except Exception as e:
            logger.error(f"加载删除申请失败: {e}")
            self.requests_data = {}

    def save_requests(self):
        try:
            atomic_write_json(self.requests_file, self.requests_data, dict)
        except Exception as e:
            logger.error(f"保存删除申请失败: {e}")

    def add_request(self, group_id: int, message_id: int, requester_id: int,
                    content: str, message_type: str, filenames: Optional[List[str]] = None) -> str:
        request_id = hashlib.md5(f"{group_id}_{message_id}_{time.time()}".encode()).hexdigest()[:8]
        content_preview = content[:200] + "..." if len(content) > 200 else content
        self.requests_data[request_id] = {
            "request_id": request_id,
            "group_id": group_id,
            "message_id": message_id,
            "requester_id": requester_id,
            "content": content,
            "content_preview": content_preview,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "filenames": filenames or self._extract_filenames(content, message_type),
            "type": message_type,
            "status": "pending",
            "request_time": time.time(),
            "process_time": None,
            "processor_id": None
        }
        self.save_requests()
        return request_id

    def _extract_filenames(self, content: str, message_type: str) -> List[str]:
        if message_type == "image":
            return [content] if content else []
        if message_type == "contribute_image" and "图片投稿:" in content:
            _, raw_names = content.split(":", 1)
            return [name.strip() for name in raw_names.split(",") if name.strip()]
        return []

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

    def clean_expired_requests(self, now: Optional[float] = None) -> int:
        current_time = now if now is not None else time.time()
        expired_keys = []
        for key, record in self.requests_data.items():
            if (record["status"] != "pending" and
                    current_time - record.get("process_time", 0) > 86400):  # 24小时
                expired_keys.append(key)
        for key in expired_keys:
            del self.requests_data[key]
        if expired_keys:
            logger.info(f"清理了 {len(expired_keys)} 个过期的删除申请")
            self.save_requests()
        return len(expired_keys)

delete_request_manager = DeleteRequestManager()
