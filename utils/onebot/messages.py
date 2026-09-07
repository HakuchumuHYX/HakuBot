"""OneBot message parsing without plugin-specific prompts or policies."""

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.helpers import extract_image_urls


def normalize_message_segments(content):
    if isinstance(content, str):
        return [{"type": "text", "data": {"text": content}}] if content else []
    if isinstance(content, (MessageSegment, dict)):
        content = [content]
    if not isinstance(content, (list, tuple, Message)):
        return []
    result = []
    for item in content or []:
        if isinstance(item, MessageSegment) or (
            isinstance(getattr(item, "type", None), str)
            and isinstance(getattr(item, "data", None), dict)
        ):
            result.append({"type": item.type, "data": dict(item.data)})
        elif isinstance(item, dict) and "type" in item:
            result.append({"type": item["type"], "data": dict(item.get("data") or {})})
        elif isinstance(item, (list, tuple, Message, str)):
            result.extend(normalize_message_segments(item))
    return result


def mentioned_users(message):
    return [
        int(s["data"]["qq"])
        for s in normalize_message_segments(message)
        if s["type"] == "at" and str(s["data"].get("qq", "")).isdigit()
    ]


def find_forward_id(message):
    return next(
        (
            str(s["data"]["id"])
            for s in normalize_message_segments(message)
            if s["type"] == "forward" and s["data"].get("id")
        ),
        None,
    )


def image_urls(event, *, reply_first=False):
    reply = getattr(event, "reply", None)
    sources = [event.get_message(), reply.message if reply else Message()]
    if reply_first:
        sources.reverse()
    for source in sources:
        urls = extract_image_urls(source)
        if urls:
            return urls
    return []


def reply_image_segments(event):
    """Only images in the replied message; never silently switch to current input."""
    reply = getattr(event, "reply", None)
    return list(reply.message["image"]) if reply is not None else []
