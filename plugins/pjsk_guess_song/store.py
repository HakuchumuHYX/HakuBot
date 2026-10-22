"""SQLite：群内积分和每日次数"""

from datetime import date

import aiosqlite

from plugins.pjsk_guess_song.config import DB_FILE

# daily.kind 为 play 或 listen；scope 在独立限额时为群号，否则为空串
_SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    score INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS daily (
    user_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    date TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_id, scope, kind)
);
"""


async def init() -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def add_score(group_id: str, user_id: str, user_name: str) -> None:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO scores (group_id, user_id, user_name, score) VALUES (?, ?, ?, 1)
            ON CONFLICT (group_id, user_id) DO UPDATE SET
                score = score + 1, user_name = excluded.user_name
            """,
            (group_id, user_id, user_name),
        )
        await db.commit()


async def leaderboard(group_id: str, limit: int) -> list[tuple[str, int]]:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT user_name, score FROM scores WHERE group_id = ? ORDER BY score DESC LIMIT ?",
            (group_id, limit),
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def daily_count(user_id: str, scope: str, kind: str) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute(
            "SELECT count FROM daily WHERE user_id = ? AND scope = ? AND kind = ? AND date = ?",
            (user_id, scope, kind, date.today().isoformat()),
        )
        row = await cursor.fetchone()
    return row[0] if row else 0


async def bump_daily(user_id: str, scope: str, kind: str) -> None:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            """
            INSERT INTO daily (user_id, scope, kind, date, count) VALUES (?, ?, ?, ?, 1)
            ON CONFLICT (user_id, scope, kind) DO UPDATE SET
                count = CASE WHEN date = excluded.date THEN count + 1 ELSE 1 END,
                date = excluded.date
            """,
            (user_id, scope, kind, date.today().isoformat()),
        )
        await db.commit()
