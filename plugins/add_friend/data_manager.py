from typing import Any
from nonebot import logger


class RequestDataManager:
    """好友请求数据管理器"""

    def __init__(self):
        self.pending_requests: dict[str, dict[str, Any]] = {}

    def add_request(self, user_id: str, user_data: dict[str, Any]) -> None:
        """添加待处理的好友请求"""
        self.pending_requests[user_id] = user_data
        logger.debug(f"已添加用户 {user_id} 的好友请求到待处理列表")

    def get_request(self, user_id: str) -> dict[str, Any]:
        """获取指定用户的好友请求数据"""
        return self.pending_requests.get(user_id)

    def remove_request(self, user_id: str) -> None:
        """移除已处理的好友请求"""
        if self.pending_requests.pop(user_id, None) is not None:
            logger.debug(f"已移除用户 {user_id} 的好友请求")

    def get_all_pending_requests(self) -> list[str]:
        """获取所有待处理的用户ID列表"""
        return list(self.pending_requests.keys())

    def has_pending_request(self, user_id: str) -> bool:
        """检查指定用户是否有待处理的请求"""
        return user_id in self.pending_requests


# 全局数据管理器实例
request_manager = RequestDataManager()
