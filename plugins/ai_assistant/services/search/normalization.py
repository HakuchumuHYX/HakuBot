import httpx
import json
import re
import math
from typing import Any, Tuple, Optional, List
from nonebot.log import logger
from pydantic import Field
from plugins.ai_assistant.config import StrictBaseModel, plugin_config
from plugins.ai_assistant.services.chat_service import call_chat_completion


def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", text or "")


def _truncate_text(text: Any, max_chars: int) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        return text[:max_chars].strip() + "..."
    return text


def _normalize_images(images: Any, *, limit: Optional[int] = None) -> List[dict]:
    if not isinstance(images, list):
        return []

    normalized: List[dict] = []
    seen = set()
    for item in images:
        if isinstance(item, str):
            url = item.strip()
            description = ""
        elif isinstance(item, dict):
            url = (item.get("url") or item.get("image_url") or "").strip()
            description = (
                item.get("description") or item.get("alt") or item.get("caption") or ""
            ).strip()
        else:
            continue

        key = url or description
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "url": _strip_control_chars(url),
                "description": _strip_control_chars(description),
            }
        )
        if limit and len(normalized) >= limit:
            break
    return normalized


def _normalize_full_result(item: dict) -> dict:
    title = (item.get("title") or "").strip()
    url = (item.get("url") or "").strip()
    content = (item.get("content") or item.get("snippet") or "").strip()
    raw_content = item.get("raw_content") or ""

    if not isinstance(raw_content, str):
        raw_content = json.dumps(raw_content, ensure_ascii=False)

    return {
        "title": _strip_control_chars(title),
        "url": _strip_control_chars(url),
        "content": _strip_control_chars(content),
        "raw_content": _strip_control_chars(raw_content),
        "score": item.get("score"),
        "favicon": _strip_control_chars((item.get("favicon") or "").strip()),
        "images": _normalize_images(item.get("images")),
    }
