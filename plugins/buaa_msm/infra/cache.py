# plugins/buaa_msm/infra/cache.py
"""
缓存（infra）

说明：
- 这里放“纯内存”的缓存实现，不应包含 NoneBot 命令/定时任务注册。
- 原实现来自 `plugins/buaa_msm/data_manage.py`，为了拆分职责迁移至此。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class UserCache:
    """用户数据缓存"""
    decrypted_data: Optional[dict] = None
    parsed_maps: Optional[dict] = None
    file_path: Optional[Path] = None
    timestamp: float = 0.0

    def is_valid(self, max_age: float = 300.0) -> bool:
        """检查缓存是否有效（默认5分钟过期）"""
        return (time.time() - self.timestamp) < max_age and self.decrypted_data is not None


class CacheManager:
    """缓存管理器"""

    def __init__(self):
        self._user_caches: Dict[str, UserCache] = {}
        self._lock = asyncio.Lock()

    async def get(self, user_id: str) -> Optional[UserCache]:
        """获取用户缓存"""
        async with self._lock:
            cache = self._user_caches.get(user_id)
            if cache and cache.is_valid():
                return cache
            return None

    async def set(self, user_id: str, decrypted_data: dict, parsed_maps: dict, file_path: Path):
        """设置用户缓存"""
        async with self._lock:
            self._user_caches[user_id] = UserCache(
                decrypted_data=decrypted_data,
                parsed_maps=parsed_maps,
                file_path=file_path,
                timestamp=time.time(),
            )

    async def invalidate(self, user_id: str):
        """使用户缓存失效"""
        async with self._lock:
            self._user_caches.pop(user_id, None)

    async def clear_all(self):
        """清除所有缓存"""
        async with self._lock:
            self._user_caches.clear()


# 全局缓存管理器实例（保持与旧版 data_manage.cache_manager 同名语义）
cache_manager = CacheManager()
