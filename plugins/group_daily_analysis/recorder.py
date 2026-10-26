"""消息记录：开了日报的群，群消息、bot 自己发的消息，以及戳一戳、撤回、贴表情事件都写进 messages.db，保留 7 天。

事件也存成一行，raw_message 里是一个自定义的伪消息段（poke / recall / reaction），由 transcript.py 转成文字。
"""

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager

from nonebot import get_driver, logger, on_message, on_notice, on_type, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    GroupRecallNoticeEvent,
    NoticeEvent,
    PokeNotifyEvent,
)
from nonebot.rule import Rule

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from core.access import switch_rule  # noqa: E402
from core.lifecycle import on_plugin_startup  # noqa: E402
from utils.logging import get_exc_desc  # noqa: E402
from plugins.group_daily_analysis.config import DATA_DIR, plugin_config  # noqa: E402

DB_PATH = DATA_DIR / "messages.db"
RETENTION_DAYS = 7
# 合并转发记录时只存前这么多个节点，转写里本来也只给一小段预览
FORWARD_NODES = 20


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
                raw_message TEXT,
                message_id TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_time ON messages (group_id, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_msg ON messages (group_id, message_id)"
        )


def _save(
    group_id, user_id, name, segments, plaintext, timestamp, message_type, message_id
):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (group_id, user_id, sender_name, content, timestamp, message_type, raw_message, message_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(group_id),
                str(user_id),
                name,
                plaintext,
                int(timestamp),
                message_type,
                json.dumps(segments, ensure_ascii=False),
                message_id,
            ),
        )


async def _save_event(event: Event, segment: dict) -> None:
    """事件没有发送者名字，转写时按 QQ 号查名字表"""
    await asyncio.to_thread(
        _save,
        event.group_id,
        event.user_id,
        None,
        [segment],
        "",
        event.time,
        segment["type"],
        None,
    )


def fetch_messages(group_id: int, start_ts: int, end_ts: int) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT user_id, sender_name, timestamp, raw_message, message_type, message_id"
            " FROM messages WHERE group_id = ? AND timestamp BETWEEN ? AND ?"
            " ORDER BY timestamp",
            (str(group_id), start_ts, end_ts),
        ).fetchall()


def fetch_by_message_ids(
    group_id: int, message_ids: set[str]
) -> dict[str, sqlite3.Row]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, sender_name, raw_message, message_id FROM messages"
            f" WHERE group_id = ? AND message_id IN ({','.join('?' * len(message_ids))})",
            (str(group_id), *message_ids),
        ).fetchall()
    return {row["message_id"]: row for row in rows}


def fetch_names(group_id: int) -> dict[str, str]:
    """QQ 号 → 库里最近一次用的名字；@、戳一戳、回复对象都按它叫"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, sender_name FROM messages"
            " WHERE group_id = ? AND sender_name IS NOT NULL ORDER BY id",
            (str(group_id),),
        ).fetchall()
    return {row["user_id"]: row["sender_name"] for row in rows}


async def _expand_forwards(bot: Bot, segments: list[dict]) -> None:
    """合并转发只给了 id，过一阵就拉不到了，所以记录时就把节点存进消息段"""
    for seg in segments:
        if seg["type"] != "forward":
            continue
        try:
            result = await bot.call_api("get_forward_msg", id=seg["data"]["id"])
        except Exception as e:
            logger.warning(f"合并转发拉取失败，转写里只留占位: {get_exc_desc(e)}")
            continue
        nodes = []
        for node in result["messages"][:FORWARD_NODES]:
            sender = node["sender"]
            name = sender.get("card") or sender.get("nickname") or "未知"
            nodes.append({"name": name, "message": node["message"]})
        seg["data"]["count"] = len(result["messages"])
        seg["data"]["nodes"] = nodes


# 优先级 1、不阻断，只旁听
message_recorder = on_message(
    rule=switch_rule("group_daily_analysis", superuser_bypass=False),
    priority=1,
    block=False,
)


@message_recorder.handle()
async def record_message(bot: Bot, event: GroupMessageEvent):
    sender = event.sender
    card = sender.card if plugin_config.enable_user_card else None
    segments = [{"type": seg.type, "data": dict(seg.data)} for seg in event.message]
    await _expand_forwards(bot, segments)
    await asyncio.to_thread(
        _save,
        event.group_id,
        event.user_id,
        card or sender.nickname or "未知用户",
        segments,
        event.get_plaintext(),
        event.time,
        "group",
        str(event.message_id),
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
async def record_message_sent(bot: Bot, event: Event):
    # 适配器没有 message_sent 的专门类型，字段都是 OneBot 原始 JSON（sender 是 dict，message 是消息段 dict 列表）
    sender = event.sender
    await _expand_forwards(bot, event.message)
    await asyncio.to_thread(
        _save,
        event.group_id,
        event.user_id,
        sender.get("card") or sender.get("nickname") or "未知用户",
        event.message,
        event.raw_message,
        event.time,
        "group_sent",
        str(event.message_id),
    )


def _is_group_poke(event: Event) -> bool:
    return isinstance(event, PokeNotifyEvent) and event.group_id is not None


def _is_group_recall(event: Event) -> bool:
    return isinstance(event, GroupRecallNoticeEvent)


def _is_emoji_like_added(event: Event) -> bool:
    # 适配器没有这个 notice 的模型，落到通用 NoticeEvent，额外字段在 model_dump 里
    return (
        isinstance(event, NoticeEvent)
        and event.notice_type == "group_msg_emoji_like"
        and event.model_dump().get("sub_type") == "add"
    )


_event_rule = switch_rule("group_daily_analysis", superuser_bypass=False)
poke_recorder = on_notice(
    rule=Rule(_is_group_poke) & _event_rule, priority=1, block=False
)
recall_recorder = on_notice(
    rule=Rule(_is_group_recall) & _event_rule, priority=1, block=False
)
reaction_recorder = on_notice(
    rule=Rule(_is_emoji_like_added) & _event_rule, priority=1, block=False
)


@poke_recorder.handle()
async def record_poke(event: PokeNotifyEvent):
    # 动作文字群友可以自定义（拍了拍、捏了捏…），照通知里的原样存
    data = event.model_dump()
    await _save_event(
        event,
        {
            "type": "poke",
            "data": {
                "target": str(event.target_id),
                "action": data.get("action") or "",
                "suffix": data.get("suffix") or "",
            },
        },
    )


@recall_recorder.handle()
async def record_recall(event: GroupRecallNoticeEvent):
    # 原消息不动，转写时给它加撤回标签；user_id 是原消息的发送者
    await _save_event(
        event,
        {
            "type": "recall",
            "data": {
                "message_id": str(event.message_id),
                "by_admin": event.operator_id != event.user_id,
            },
        },
    )


@reaction_recorder.handle()
async def record_reaction(event: NoticeEvent):
    data = event.model_dump()
    await _save_event(
        event,
        {
            "type": "reaction",
            "data": {
                "message_id": str(data["message_id"]),
                "emoji_ids": [
                    str(like["emoji_id"]) for like in data.get("likes") or []
                ],
            },
        },
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
