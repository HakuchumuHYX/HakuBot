"""消息记录：开了日报的群，群消息和 bot 自己发的消息都写进 messages.db，保留 7 天"""

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager

from nonebot import get_driver, logger, on_message, on_type, require
from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from core.access import switch_rule  # noqa: E402
from core.lifecycle import on_plugin_startup  # noqa: E402
from plugins.group_daily_analysis.config import DATA_DIR, plugin_config  # noqa: E402

DB_PATH = DATA_DIR / "messages.db"
RETENTION_DAYS = 7


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@on_plugin_startup(get_driver(), "group_daily_analysis")
def _init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                sender_name TEXT,
                content TEXT,
                timestamp INTEGER NOT NULL,
                message_type TEXT,
                raw_message TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_time ON messages (group_id, timestamp)"
        )


def _save(group_id, user_id, name, segments, plaintext, timestamp, message_type):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (group_id, user_id, sender_name, content, timestamp, message_type, raw_message)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(group_id),
                str(user_id),
                name,
                plaintext,
                int(timestamp),
                message_type,
                json.dumps(segments, ensure_ascii=False),
            ),
        )


def fetch_messages(group_id: int, start_ts: int, end_ts: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT user_id, sender_name, timestamp, raw_message FROM messages"
            " WHERE group_id = ? AND timestamp BETWEEN ? AND ? ORDER BY timestamp",
            (str(group_id), start_ts, end_ts),
        ).fetchall()


# 优先级 1、不阻断，只旁听
message_recorder = on_message(
    rule=switch_rule("group_daily_analysis", superuser_bypass=False),
    priority=1,
    block=False,
)


@message_recorder.handle()
async def record_message(event: GroupMessageEvent):
    sender = event.sender
    card = sender.card if plugin_config.enable_user_card else None
    segments = [{"type": seg.type, "data": dict(seg.data)} for seg in event.message]
    await asyncio.to_thread(
        _save,
        event.group_id,
        event.user_id,
        card or sender.nickname or "未知用户",
        segments,
        event.get_plaintext(),
        event.time,
        "group",
    )


def _is_group_sent(event: Event) -> bool:
    return (
        getattr(event, "post_type", None) == "message_sent"
        and getattr(event, "message_type", None) == "group"
    )


message_sent_recorder = on_type(
    Event,
    rule=Rule(_is_group_sent)
    & switch_rule("group_daily_analysis", superuser_bypass=False),
    priority=1,
    block=False,
)


@message_sent_recorder.handle()
async def record_message_sent(event: Event):
    # 适配器没有 message_sent 的专门类型，字段都是 OneBot 原始 JSON（sender 是 dict，message 是消息段 dict 列表）
    sender = event.sender
    await asyncio.to_thread(
        _save,
        event.group_id,
        event.user_id,
        sender.get("card") or sender.get("nickname") or "未知用户",
        event.message,
        event.raw_message,
        event.time,
        "group_sent",
    )


@scheduler.scheduled_job(
    "cron", hour=4, minute=0, id="group_daily_analysis_cleanup_job"
)
def cleanup_old_messages() -> None:
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with _connect() as conn:
        deleted = conn.execute(
            "DELETE FROM messages WHERE timestamp < ?", (cutoff,)
        ).rowcount
    if deleted:
        with _connect() as conn:
            conn.execute("VACUUM")
        logger.info(f"已清理 {deleted} 条过期消息 (保留 {RETENTION_DAYS} 天)")
