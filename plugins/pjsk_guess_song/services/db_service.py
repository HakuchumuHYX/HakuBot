# pjsk_guess_song/services/db_service.py

import aiosqlite
import json
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# --- Nonebot Imports ---
from nonebot.log import logger


# -------------------------

class DBService:
    def __init__(self, db_path: str):
        self.db_path = db_path
        logger.info(f"数据库服务已初始化，路径: {self.db_path}")

    def _get_conn(self) -> aiosqlite.Connection:
        """
        [正确模式] 返回一个 aiosqlite 连接对象（Awaitable），
        由调用方的 `async with` 来管理其生命周期。
        """
        return aiosqlite.connect(self.db_path)

    async def _ensure_user_exists(self, cursor: aiosqlite.Cursor, user_id: str, user_name: str):
        """确保用户在数据库中存在，如果不存在则创建。"""
        await cursor.execute("SELECT 1 FROM user_stats WHERE user_id = ?", (user_id,))
        if await cursor.fetchone() is None:
            today = datetime.now().strftime("%Y-%m-%d")
            columns = [
                "user_id", "user_name", "last_played_date", "daily_games_played",
                "last_listen_date", "daily_listen_songs", "group_daily_plays"
            ]
            default_values = (user_id, user_name, today, 0, today, 0, '{}')
            placeholders = ','.join(['?'] * len(columns))
            await cursor.execute(f"INSERT INTO user_stats ({', '.join(columns)}) VALUES ({placeholders})",
                                 default_values)
            logger.debug(f"新用户 {user_id} ({user_name}) 已创建。")

    async def init_db(self):
        """初始化数据库，创建简化表结构。"""
        async with self._get_conn() as conn:
            # 简化用户表，只保留基本游戏记录
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_stats (
                    user_id TEXT PRIMARY KEY, 
                    user_name TEXT, 
                    last_played_date TEXT, 
                    daily_games_played INTEGER DEFAULT 0,
                    last_listen_date TEXT, 
                    daily_listen_songs INTEGER DEFAULT 0,
                    group_daily_plays TEXT 
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_scores (
                    user_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    score INTEGER DEFAULT 0,
                    user_name TEXT,
                    PRIMARY KEY (user_id, group_id)
                )
            """)
            await conn.commit()
            logger.info("数据库表 'user_stats' 和 'user_scores' 已确认存在。")

    async def consume_daily_play_attempt(self, user_id: str, user_name: str, session_id: str, is_independent: bool):
        """根据是否为独立限制模式，消耗用户的每日游戏次数。"""
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await self._ensure_user_exists(cursor, user_id, user_name)
                today = datetime.now().strftime("%Y-%m-%d")

                if is_independent:
                    await cursor.execute("SELECT group_daily_plays FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    group_plays = json.loads(row['group_daily_plays'] or '{}')
                    group_stat = group_plays.get(session_id, {})

                    current_count = group_stat.get('count', 0) if group_stat.get('date') == today else 0
                    group_plays[session_id] = {'count': current_count + 1, 'date': today}

                    await cursor.execute("UPDATE user_stats SET group_daily_plays = ?, user_name = ? WHERE user_id = ?",
                                         (json.dumps(group_plays), user_name, user_id))
                else:
                    await cursor.execute(
                        "SELECT daily_games_played, last_played_date FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    daily_games = row['daily_games_played'] if row and row['last_played_date'] == today else 0
                    await cursor.execute(
                        "UPDATE user_stats SET daily_games_played = ?, last_played_date = ?, user_name = ? WHERE user_id = ?",
                        ((daily_games or 0) + 1, today, user_name, user_id))
                await conn.commit()

    async def can_play(self, user_id: str, daily_limit: int, session_id: str, is_independent: bool) -> bool:
        """根据是否为独立限制模式，检查用户是否可以开始游戏。"""
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                today = datetime.now().strftime("%Y-%m-%d")

                if is_independent:
                    await cursor.execute("SELECT group_daily_plays FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    if not row or not row['group_daily_plays']:
                        return True

                    group_plays = json.loads(row['group_daily_plays'])
                    group_stat = group_plays.get(session_id, {})
                    if group_stat.get('date') != today:
                        return True
                    return group_stat.get('count', 0) < daily_limit
                else:
                    await cursor.execute(
                        "SELECT daily_games_played, last_played_date FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    if not row or row['last_played_date'] != today:
                        return True
                    return (row['daily_games_played'] or 0) < daily_limit

    async def get_games_played_today(self, user_id: str, session_id: str, is_independent: bool) -> int:
        """获取用户今天已玩的游戏次数，能自动处理独立模式和全局模式。"""
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                today = datetime.now().strftime("%Y-%m-%d")
                if is_independent:
                    await cursor.execute("SELECT group_daily_plays FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    if not row or not row['group_daily_plays']: return 0

                    group_plays = json.loads(row['group_daily_plays'])
                    group_stat = group_plays.get(session_id, {})
                    return group_stat.get('count', 0) if group_stat.get('date') == today else 0
                else:
                    await cursor.execute(
                        "SELECT daily_games_played, last_played_date FROM user_stats WHERE user_id = ?", (user_id,))
                    row = await cursor.fetchone()
                    if not row or row['last_played_date'] != today: return 0
                    return row['daily_games_played'] or 0

    async def record_listen_song(self, user_id: str, user_name: str):
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await self._ensure_user_exists(cursor, user_id, user_name)
                await cursor.execute("SELECT daily_listen_songs, last_listen_date FROM user_stats WHERE user_id = ?",
                                     (user_id,))
                row = await cursor.fetchone()

                today = datetime.now().strftime("%Y-%m-%d")
                daily_listen = row['daily_listen_songs'] if row and row['last_listen_date'] == today else 0

                await cursor.execute(
                    "UPDATE user_stats SET daily_listen_songs = ?, last_listen_date = ?, user_name = ? WHERE user_id = ?",
                    ((daily_listen or 0) + 1, today, user_name, user_id))
                await conn.commit()

    async def can_listen_song(self, user_id: str, daily_limit: int) -> bool:
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT daily_listen_songs, last_listen_date FROM user_stats WHERE user_id = ?",
                                     (user_id,))
                row = await cursor.fetchone()
                if not row or row['last_listen_date'] != datetime.now().strftime("%Y-%m-%d"):
                    return True
                return (row['daily_listen_songs'] or 0) < daily_limit

    async def get_user_daily_limits(self, user_id: str) -> Tuple[bool, int]:
        """(原版遗留函数，似乎未在 main.py 中使用)"""
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT daily_listen_songs, last_listen_date FROM user_stats WHERE user_id = ?",
                                     (user_id,))
                row = await cursor.fetchone()
                if not row or row['last_listen_date'] != datetime.now().strftime("%Y-%m-%d"):
                    return True, 0
                return True, (row['daily_listen_songs'] or 0)

    async def reset_guess_limit(self, target_id: str) -> bool:
        """(原版遗留函数，似乎未在 main.py 中使用)"""
        async with self._get_conn() as conn:
            res = await conn.execute("UPDATE user_stats SET daily_games_played = 0 WHERE user_id = ?", (target_id,))
            await conn.commit()
            return res.rowcount > 0

    async def reset_listen_limit(self, target_id: str) -> bool:
        """(原版遗留函数，似乎未在 main.py 中使用)"""
        async with self._get_conn() as conn:
            res = await conn.execute("UPDATE user_stats SET daily_listen_songs = 0 WHERE user_id = ?", (target_id,))
            await conn.commit()
            return res.rowcount > 0

    async def add_score(self, user_id: str, group_id: str, score_to_add: int, user_name: str):
        """
        为指定群聊中的指定用户增加分数 (UPSERT)
        """
        async with self._get_conn() as conn:
            async with conn.cursor() as cursor:
                # 使用 UPSERT 语法 (INSERT... ON CONFLICT... DO UPDATE)
                # 确保 user_id 和 group_id 有一个复合主键
                await cursor.execute("""
                    INSERT INTO user_scores (user_id, group_id, score, user_name)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, group_id) DO UPDATE SET
                        score = score + excluded.score,
                        user_name = excluded.user_name;
                """, (user_id, group_id, score_to_add, user_name))
                await conn.commit()

    async def get_group_leaderboard(self, group_id: str, limit: int = 10) -> List[Tuple[str, int]]:
        """
        获取指定群聊的排行榜
        """
        async with self._get_conn() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT user_name, score FROM user_scores
                    WHERE group_id = ?
                    ORDER BY score DESC
                    LIMIT ?
                """, (group_id, limit))
                rows = await cursor.fetchall()
                # 转换数据格式为 (user_name, score) 元组列表
                return [(row['user_name'], row['score']) for row in rows]