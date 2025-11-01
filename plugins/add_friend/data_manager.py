from typing import Dict, Any, List
from nonebot.log import logger
import time


class RequestDataManager:
    """好友请求数据管理器"""

    def __init__(self):
        self.pending_requests: Dict[str, Dict[str, Any]] = {}
        self.processed_requests: Dict[str, float] = {}  # 记录已处理的请求
        self.cache_expire_time = 300  # 5分钟

    def add_request(self, user_id: str, user_data: Dict[str, Any]) -> bool:
        """添加待处理的好友请求"""
        try:
            self.pending_requests[user_id] = user_data
            logger.info(f"已添加用户 {user_id} 的好友请求到待处理列表")
            return True
        except Exception as e:
            logger.error(f"添加好友请求失败: {e}")
            return False

    def get_request(self, user_id: str) -> Dict[str, Any]:
        """获取指定用户的好友请求数据"""
        return self.pending_requests.get(user_id)

    def remove_request(self, user_id: str) -> bool:
        """移除已处理的好友请求"""
        try:
            if user_id in self.pending_requests:
                del self.pending_requests[user_id]
                logger.info(f"已移除用户 {user_id} 的好友请求")
                return True
            return False
        except Exception as e:
            logger.error(f"移除好友请求失败: {e}")
            return False

    def get_all_pending_requests(self) -> List[str]:
        """获取所有待处理的用户ID列表"""
        return list(self.pending_requests.keys())

    def has_pending_request(self, user_id: str) -> bool:
        """检查指定用户是否有待处理的请求"""
        return user_id in self.pending_requests

    def mark_request_processed(self, request_key: str):
        """标记请求为已处理"""
        self.processed_requests[request_key] = time.time()

    def is_request_processed(self, request_key: str) -> bool:
        """检查请求是否已处理"""
        current_time = time.time()

        # 清理过期的记录
        expired_keys = [k for k, t in self.processed_requests.items()
                        if current_time - t > self.cache_expire_time]
        for key in expired_keys:
            del self.processed_requests[key]

        return request_key in self.processed_requests

    def clear_all_requests(self):
        """清空所有待处理请求（用于调试或重置）"""
        self.pending_requests.clear()
        logger.info("已清空所有待处理的好友请求")


# 全局数据管理器实例
request_manager = RequestDataManager()