# managers/poke_cd_manager.py
import time
from typing import Dict, Tuple
from nonebot import logger

from ..config import get_poke_cd_time, is_poke_cd_enabled

class PokeCDManager:
    def __init__(self):
        self.user_cd: Dict[Tuple[int, int], float] = {}  # (group_id, user_id) -> last_poke_time

    def check_cd(self, group_id: int, user_id: int) -> Tuple[bool, float]:
        """
        检查用户CD状态

        Returns:
            Tuple[bool, float]: (是否在CD中, 剩余CD时间)
        """
        if not is_poke_cd_enabled(group_id):
            return False, 0

        cd_time = get_poke_cd_time()
        key = (group_id, user_id)
        current_time = time.time()

        if key in self.user_cd:
            last_time = self.user_cd[key]
            elapsed = current_time - last_time
            if elapsed < cd_time:
                return True, cd_time - elapsed

        # 不在CD中或CD已结束，更新最后戳一戳时间
        self.user_cd[key] = current_time
        return False, 0

    def clear_expired_cd(self, expire_seconds: int = 3600):
        """清理过期的CD记录（超过1小时未活动的记录）"""
        current_time = time.time()
        expired_keys = []

        for key, last_time in self.user_cd.items():
            if current_time - last_time > expire_seconds:
                expired_keys.append(key)

        for key in expired_keys:
            del self.user_cd[key]

        if expired_keys:
            logger.debug(f"清理了 {len(expired_keys)} 个过期的戳一戳CD记录")


# 全局CD管理器实例
poke_cd_manager = PokeCDManager()