"""歌单管理模块（sqlite 本地持久化）"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from nonebot.log import logger

from .model import Song


class Playlist:
    """歌单管理类，封装歌单的所有操作"""

    def __init__(self, db_path: Path, limit: int):
        self.db_path = db_path
        self.limit = limit

        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self):
        """初始化数据库表"""
        async with self._lock:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            cursor = self._conn.cursor()

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS playlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    song_id TEXT NOT NULL,
                    song_name TEXT,
                    artists TEXT,
                    duration INTEGER,
                    cover_url TEXT,
                    audio_url TEXT,
                    platform TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, song_id, platform)
                )
            """
            )

            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_id ON playlist(user_id)
            """
            )

            self._conn.commit()
            logger.info("[music_plugin] 歌单数据库初始化完成")

    async def close(self):
        """关闭数据库连接"""
        async with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    async def add_song(self, user_id: str, song: Song, platform: str) -> bool:
        """添加歌曲到歌单"""
        async with self._lock:
            if not self._conn:
                return False
            try:
                cursor = self._conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO playlist
                    (user_id, song_id, song_name, artists, duration, cover_url, audio_url, platform)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        song.id,
                        song.name,
                        song.artists,
                        song.duration,
                        song.cover_url,
                        song.audio_url,
                        platform,
                    ),
                )
                self._conn.commit()
                logger.debug(f"[music_plugin] 用户 {user_id} 收藏了歌曲：{song.name}")
                return True
            except sqlite3.IntegrityError:
                return False
            except Exception as e:
                logger.error(f"[music_plugin] 添加歌曲到歌单失败: {e}")
                return False

    async def remove_song(self, user_id: str, song_id: str, platform: str) -> bool:
        """从歌单移除歌曲"""
        async with self._lock:
            if not self._conn:
                return False
            try:
                cursor = self._conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM playlist
                    WHERE user_id = ? AND song_id = ? AND platform = ?
                    """,
                    (user_id, song_id, platform),
                )
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"[music_plugin] 从歌单移除歌曲失败: {e}")
                return False

    async def get_songs(self, user_id: str, limit: int | None = None) -> list[tuple[Song, str]]:
        """获取用户的歌单"""
        if limit is None:
            limit = self.limit

        async with self._lock:
            if not self._conn:
                return []
            try:
                cursor = self._conn.cursor()
                cursor.execute(
                    """
                    SELECT song_id, song_name, artists, duration, cover_url, audio_url, platform
                    FROM playlist
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )

                rows = cursor.fetchall()
                result: list[tuple[Song, str]] = []
                for row in rows:
                    song = Song(
                        id=str(row["song_id"]),
                        name=row["song_name"],
                        artists=row["artists"],
                        duration=row["duration"],
                        cover_url=row["cover_url"],
                        audio_url=row["audio_url"],
                    )
                    platform = row["platform"]
                    result.append((song, platform))
                return result
            except Exception as e:
                logger.error(f"[music_plugin] 获取用户歌单失败: {e}")
                return []

    async def is_empty(self, user_id: str) -> bool:
        """检查用户歌单是否为空"""
        async with self._lock:
            if not self._conn:
                return True
            try:
                cursor = self._conn.cursor()
                cursor.execute("SELECT 1 FROM playlist WHERE user_id = ? LIMIT 1", (user_id,))
                row = cursor.fetchone()
                return row is None
            except Exception as e:
                logger.error(f"[music_plugin] 检查歌单是否为空失败: {e}")
                return True
