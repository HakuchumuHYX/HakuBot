# plugins/buaa_msm/services/processing_guard.py
"""
并发处理保护：防止同一用户重复触发分析流程。

说明：
- 原先逻辑在 plugins/buaa_msm/__init__.py 中，这里抽成 service，供 handlers/upload/msr 复用。
"""

from __future__ import annotations

import asyncio
from typing import Set

_processing_lock = asyncio.Lock()
_processing_users: Set[str] = set()


async def is_processing(user_id: str) -> bool:
    async with _processing_lock:
        return user_id in _processing_users


async def set_processing(user_id: str, value: bool) -> None:
    async with _processing_lock:
        if value:
            _processing_users.add(user_id)
        else:
            _processing_users.discard(user_id)
