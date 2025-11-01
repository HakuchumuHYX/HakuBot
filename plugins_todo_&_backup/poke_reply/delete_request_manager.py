# managers/delete_request_manager.py
import json
import time
import hashlib
from pathlib import Path
from typing import Dict, Optional, List
from nonebot import logger

from ..config import PLUGIN_DIR
from ..utils.common import get_group_id

# 文件路径
DELETE_REQUESTS_FILE = PLUGIN_DIR / "delete_requests.json"


class DeleteRequestManager:
    def __init__(self):
        self.requests_file = DELETE_REQUESTS_FILE
        self.requests_data: Dict[str, dict] = {}
        self.load_requests()

    def load_requests(self):
        """加载删除申请数据"""
        try:
            if self.requests_file.exists():
                with open(self.requests_file, 'r', encoding='utf-8') as f:
                    self.requests_data = json.load(f)
                logger.info(f"删除申请加载成功，共 {len(self.requests_data)} 条记录")
            else:
                self.requests_data = {}
                self.save_requests()
        except Exception as e:
            logger.error(f"加载删除申请失败: {e}")
            self.requests_data = {}

    def save_requests(self):
        """保存删除申请数据"""
        try:
            self.requests_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.requests_file, 'w', encoding='utf-8') as f:
                json.dump(self.requests_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存删除申请失败: {e}")

    def add_request(self, group_id: int, message_id: int, requester_id: int,
                    content: str, message_type: str) -> str:
        """
        添加删除申请

        Returns:
            str: 申请ID
        """
        request_id = hashlib.md5(f"{group_id}_{message_id}_{time.time()}".encode()).hexdigest()[:8]

        self.requests_data[request_id] = {
            "request_id": request_id,
            "group_id": group_id,
            "message_id": message_id,
            "requester_id": requester_id,
            "content": content[:200] + "..." if len(content) > 200 else content,  # 截断长内容
            "type": message_type,
            "status": "pending",  # pending, approved, rejected
            "request_time": time.time(),
            "process_time": None,
            "processor_id": None
        }

        self.save_requests()
        logger.info(f"已添加删除申请: ID={request_id}, 群组={group_id}, 消息ID={message_id}")
        return request_id

    def get_pending_requests(self) -> List[dict]:
        """获取待处理的申请"""
        return [req for req in self.requests_data.values() if req["status"] == "pending"]

    def get_request(self, request_id: str) -> Optional[dict]:
        """获取申请信息"""
        return self.requests_data.get(request_id)

    def update_request(self, request_id: str, status: str, processor_id: int) -> bool:
        """更新申请状态"""
        if request_id in self.requests_data:
            self.requests_data[request_id].update({
                "status": status,
                "process_time": time.time(),
                "processor_id": processor_id
            })
            self.save_requests()
            logger.info(f"已更新删除申请状态: ID={request_id}, 状态={status}")
            return True
        return False

    def remove_request(self, request_id: str) -> bool:
        """移除删除申请"""
        if request_id in self.requests_data:
            del self.requests_data[request_id]
            self.save_requests()
            logger.info(f"已移除删除申请: ID={request_id}")
            return True
        return False


# 全局实例
delete_request_manager = DeleteRequestManager()