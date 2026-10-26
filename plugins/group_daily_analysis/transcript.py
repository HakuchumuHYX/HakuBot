"""消息库的行 → 统计和给 LLM 的转写。

各类消息段都转成文字（图片只写占位，不给 LLM 看）。回复带上原文，撤回、贴表情是单独记的事件，挂回原消息上。
消息段的写法参考 HakuBot-autochat 的 nyaturingtest（messages/convert.py、faces.py）。
"""

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from utils.logging import get_exc_desc
from plugins.group_daily_analysis.config import plugin_config
from plugins.group_daily_analysis.models import Statistics
from plugins.group_daily_analysis.recorder import fetch_by_message_ids, fetch_names

EMOJI_TYPES = {"face", "mface", "bface", "sface"}
# 消息库里除了这两种，其余是戳一戳、撤回、贴表情这类事件，不算消息
MESSAGE_TYPES = {"group", "group_sent"}
CARD_CHARS = 120
REPLY_CHARS = 40
FORWARD_CHARS = 120

# QQ 表情编号 → 名字，第一次出日报时向 SnowLuma 拉一次
_face_names: dict[str, str] = {}


async def load_faces(bot: Bot) -> None:
    """失败就只写 [表情]，下次出日报再试"""
    if _face_names:
        return
    try:
        result = await bot.call_api("fetch_sys_faces")
    except Exception as e:
        logger.warning(f"获取 QQ 表情目录失败，表情只写 [表情]: {get_exc_desc(e)}")
        return
    for pack in result["packs"]:
        for entry in pack["emojis"]:
            # emoji 分组的 q_sid 是字符本身，收到时是码点，face_name 直接转
            if entry["q_sid"].isdigit():
                _face_names.setdefault(entry["q_sid"], entry["q_des"].lstrip("/"))


def _face_name(face_id: str) -> str:
    """超过 3 位的是 emoji 码点；查不到返回空字符串"""
    if len(face_id) > 3 and face_id.isdigit():
        return chr(int(face_id))
    return _face_names.get(face_id, "")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _card_text(raw: str) -> str:
    """prompt 是「[QQ小程序]标题」这类摘要，meta 里补上 prompt 没写到的标题和简介"""
    try:
        card = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"卡片 JSON 解析失败: {e}")
        return "[卡片]"
    prompt = str(card.get("prompt") or "").strip()
    extras = []
    meta = card.get("meta")
    for item in meta.values() if isinstance(meta, dict) else []:
        if not isinstance(item, dict):
            continue
        for key in ("title", "desc"):
            value = str(item.get(key) or "").strip()
            if value and value not in prompt and value not in extras:
                extras.append(value)
    text = " ".join([prompt, *extras]).strip()
    return f"[卡片: {_cut(text, CARD_CHARS)}]" if text else "[卡片]"


def _image_text(data: dict) -> str:
    # 表情包的 summary 是 [动画表情]，商城表情是它的名字（如 [呵呵]）
    summary = str(data.get("summary") or "")
    if summary == "[动画表情]":
        return "[表情包]"
    if summary in ("", "[图片]"):
        return "[图片]"
    return f"[表情:{summary.strip('[]')}]"


def _forward_text(data: dict, names: dict[str, str]) -> str:
    """节点是记录时拉下来存进去的（recorder._expand_forwards）；没拉到就只有占位"""
    nodes = data.get("nodes")
    if not nodes:
        return "[合并转发]"
    lines = []
    for node in nodes:
        text = "".join(segment_text(seg, names) for seg in node["message"]).strip()
        if text:
            lines.append(f"{node['name']}: {text}")
    return f"[合并转发 {data['count']} 条: {_cut(' / '.join(lines), FORWARD_CHARS)}]"


def segment_text(seg: dict, names: dict[str, str], replies: dict | None = None) -> str:
    """一个消息段 → 文字。replies 为 None 时不展开回复（被回复的原文里再套回复就不管了）"""
    kind, data = seg["type"], seg["data"]
    if kind == "text":
        return data["text"]
    if kind == "at":
        qq = str(data.get("qq", ""))
        return "@全体成员" if qq == "all" else f"@{names.get(qq, '某群友')}"
    if kind == "image":
        return _image_text(data)
    if kind == "face":
        name = _face_name(str(data.get("id", "")))
        return f"[表情:{name}]" if name else "[表情]"
    if kind == "json":
        return _card_text(data.get("data") or "")
    if kind == "record":
        return "[语音]"
    if kind == "video":
        return "[视频]"
    if kind == "file":
        return f"[文件: {data.get('name') or '未知'}]"
    if kind == "forward":
        return _forward_text(data, names)
    if kind == "reply" and replies is not None:
        row = replies.get(str(data.get("id")))
        if row is None:
            return "[回复] "
        name = (row["sender_name"] or "").strip() or names.get(row["user_id"], "某群友")
        text = "".join(
            segment_text(s, names) for s in json.loads(row["raw_message"])
        ).strip()
        return f"[回复 {name}: {_cut(text, REPLY_CHARS)}] "
    if kind == "poke":
        target = names.get(data["target"], "某群友")
        return f"[戳一戳: {data['action'] or '戳了戳'} {target}{data['suffix']}]"
    # markdown、inline_keyboard 是别的 bot 发的按钮消息
    return ""


@dataclass
class UserFeature:
    name: str
    message_count: int = 0
    character_count: int = 0
    emoji_count: int = 0
    at_count: int = 0
    night_message_count: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class Line:
    at: datetime
    name: str
    text: str

    def __str__(self) -> str:
        return f"[{self.at:%H:%M}] {self.name}: {self.text}"


@dataclass
class Transcript:
    lines: list[Line] = field(default_factory=list)
    users: dict[str, UserFeature] = field(default_factory=dict)


def _is_emoji(seg: dict) -> bool:
    return seg["type"] in EMOJI_TYPES or (
        seg["type"] == "image" and "表情" in seg["data"].get("summary", "")
    )


def read_messages(group_id: int, rows) -> tuple[Statistics, Transcript]:
    """统计只算消息；转写去掉 bot 和命令。会查库（名字表、窗口外被回复的消息），调用方放进线程跑"""
    names = fetch_names(group_id)
    parsed = [(row, json.loads(row["raw_message"])) for row in rows]

    # 先收事件：撤回和贴表情按 message_id 挂回原消息
    recalled: dict[str, bool] = {}
    reactions: dict[str, Counter] = defaultdict(Counter)
    by_id = {}
    reply_ids = set()
    for row, segments in parsed:
        if row["message_type"] == "recall":
            data = segments[0]["data"]
            recalled[data["message_id"]] = data["by_admin"]
        elif row["message_type"] == "reaction":
            data = segments[0]["data"]
            for emoji_id in data["emoji_ids"]:
                reactions[data["message_id"]][_face_name(emoji_id) or "未知"] += 1
        elif row["message_id"]:
            by_id[row["message_id"]] = row
        reply_ids |= {
            str(s["data"]["id"])
            for s in segments
            if s["type"] == "reply" and s["data"].get("id")
        }
    # 回复窗口外（比如前一天）的消息，一次查回来
    if missing := reply_ids - by_id.keys():
        by_id |= fetch_by_message_ids(group_id, missing)

    stats = Statistics()
    transcript = Transcript()
    participants = set()
    bot_ids = set(plugin_config.bot_qq_ids)
    for row, segments in parsed:
        kind = row["message_type"]
        if kind not in MESSAGE_TYPES and kind != "poke":
            continue
        user_id = row["user_id"]
        sent_at = datetime.fromtimestamp(row["timestamp"])
        if kind in MESSAGE_TYPES:
            stats.message_count += 1
            participants.add(user_id)
            stats.hourly[sent_at.hour] += 1
            stats.emoji_count += sum(map(_is_emoji, segments))
            stats.total_characters += sum(
                len(s["data"]["text"]) for s in segments if s["type"] == "text"
            )

        content = "".join(segment_text(s, names, by_id) for s in segments)
        content = content.strip().replace('"', "'").replace("\n", " ")
        if user_id in bot_ids or len(content) <= 1 or content.startswith("/"):
            continue
        if row["message_id"] in recalled:
            tag = "[被管理员撤回]" if recalled[row["message_id"]] else "[已撤回]"
            content = f"{tag} {content}"
        if counts := reactions.get(row["message_id"]):
            marks = " ".join(f"{k}×{n}" if n > 1 else k for k, n in counts.items())
            content += f" [被贴表情: {marks}]"

        name = (row["sender_name"] or "").strip() or names.get(user_id, "群友")
        transcript.lines.append(Line(sent_at, name, content))
        if kind == "poke":
            continue
        # 称号用的特征只看文字，占位不算字数、不当代表发言
        plain = "".join(s["data"]["text"] for s in segments if s["type"] == "text")
        plain = plain.strip().replace("\n", " ")
        user = transcript.users.setdefault(user_id, UserFeature(name=name))
        user.name = name
        user.message_count += 1
        user.character_count += len(plain)
        user.emoji_count += sum(map(_is_emoji, segments))
        user.at_count += sum(s["type"] == "at" for s in segments)
        if sent_at.hour >= 23 or sent_at.hour < 6:
            user.night_message_count += 1
        if len(plain) > 1 and len(user.samples) < 5:
            user.samples.append(plain)

    stats.participant_count = len(participants)
    return stats, transcript
