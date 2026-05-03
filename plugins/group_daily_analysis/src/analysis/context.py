from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TextMessage:
    time: str
    timestamp: int
    sender: str
    user_id: str
    content: str


@dataclass
class UserFeature:
    user_id: str
    name: str
    message_count: int = 0
    character_count: int = 0
    emoji_count: int = 0
    at_count: int = 0
    night_message_count: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def average_length(self) -> float:
        if self.message_count <= 0:
            return 0.0
        return self.character_count / self.message_count


@dataclass
class QuoteCandidate:
    content: str
    sender: str
    user_id: str
    score: int


@dataclass
class TranscriptContext:
    text_messages: list[TextMessage]
    messages_text: str
    user_features: dict[str, UserFeature]
    quote_candidates: list[QuoteCandidate]


def _message_time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _is_night_hour(ts: int) -> bool:
    hour = datetime.fromtimestamp(ts).hour
    return hour >= 23 or hour < 6


def _sender_name(sender: dict[str, Any]) -> str:
    return (sender.get("card") or sender.get("nickname") or "群友").strip() or "群友"


def _extract_text_and_counts(segments: list[dict[str, Any]]) -> tuple[str, int, int]:
    content_parts: list[str] = []
    emoji_count = 0
    at_count = 0

    for seg in segments:
        seg_type = seg.get("type")
        data = seg.get("data") or {}
        if seg_type == "text":
            content_parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            at_count += 1
            content_parts.append(f"@{data.get('qq', '')}")
        elif seg_type in {"face", "mface", "bface", "sface"}:
            emoji_count += 1
        elif seg_type == "image":
            summary = str(data.get("summary", ""))
            if "表情" in summary:
                emoji_count += 1

    return "".join(content_parts).strip(), emoji_count, at_count


def _clean_content(content: str) -> str:
    return content.replace('"', "'").replace("\n", " ").strip()


def _quote_score(content: str, emoji_count: int, at_count: int) -> int:
    score = min(len(content), 120)
    score += emoji_count * 12
    score += at_count * 8
    for marker in ("？", "?", "！", "!"):
        if marker in content:
            score += 24
    for marker in ("草", "逆天", "笑死", "什么"):
        if marker in content:
            score += 10
    return score


def build_transcript_context(
    messages: list[dict[str, Any]],
    *,
    bot_ids: list[str] | None = None,
    max_user_samples: int = 5,
    quote_candidate_limit: int = 80,
) -> TranscriptContext:
    bot_id_set = {str(x) for x in (bot_ids or [])}
    text_messages: list[TextMessage] = []
    user_features: dict[str, UserFeature] = {}
    quote_candidates: list[QuoteCandidate] = []

    for msg in messages:
        sender = msg.get("sender") or {}
        user_id = str(sender.get("user_id", ""))
        if not user_id or user_id in bot_id_set:
            continue

        ts = int(msg.get("time") or 0)
        name = _sender_name(sender)
        content, emoji_count, at_count = _extract_text_and_counts(msg.get("message") or [])
        content = _clean_content(content)
        if len(content) <= 1 or content.startswith("/"):
            continue

        feature = user_features.setdefault(user_id, UserFeature(user_id=user_id, name=name))
        feature.name = name
        feature.message_count += 1
        feature.character_count += len(content)
        feature.emoji_count += emoji_count
        feature.at_count += at_count
        if _is_night_hour(ts):
            feature.night_message_count += 1
        if len(feature.samples) < max_user_samples:
            feature.samples.append(content)

        text_msg = TextMessage(
            time=_message_time(ts),
            timestamp=ts,
            sender=name,
            user_id=user_id,
            content=content,
        )
        text_messages.append(text_msg)
        quote_candidates.append(
            QuoteCandidate(
                content=content,
                sender=name,
                user_id=user_id,
                score=_quote_score(content, emoji_count, at_count),
            )
        )

    messages_text = "\n".join(
        f"[{msg.time}] {msg.sender}: {msg.content}" for msg in text_messages
    )
    quote_candidates.sort(key=lambda item: item.score, reverse=True)

    return TranscriptContext(
        text_messages=text_messages,
        messages_text=messages_text,
        user_features=user_features,
        quote_candidates=quote_candidates[:quote_candidate_limit],
    )
