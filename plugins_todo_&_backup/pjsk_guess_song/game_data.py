# pjsk_guess_song/game_data.py
"""
存放全局游戏状态
"""
import asyncio
from collections import defaultdict
from typing import Dict

# 游戏会话锁
game_session_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# 活跃的游戏会话
# 这是插件的核心状态，存储所有正在进行的游戏
active_game_sessions: Dict[str, Dict] = {}

# 游戏结束时间戳
last_game_end_time: Dict[str, float] = {}