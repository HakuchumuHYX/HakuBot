from typing import Dict, Any, List
from nonebot.log import logger


class RequestDataManager:
    """好友请求数据管理器"""

    def __init__(self):
        self.pending_requests: Dict[str, Dict[str, Any]] = {}

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


# 全局数据管理器实例
request_manager = RequestDataManager()
