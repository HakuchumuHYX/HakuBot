from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from .models import PixivIllust, PixivPage


_PID_PATTERN = re.compile(
    r"(?:(?:https?://)?(?:www\.)?pixiv\.net/(?:en/)?artworks/)?(\d+)"
)


@dataclass(frozen=True)
class PixivPolicy:
    allow_r18: bool = False
    allow_r18g: bool = False


def parse_pid(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None

    match = _PID_PATTERN.fullmatch(text)
    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def policy_allows(illust: PixivIllust, policy: PixivPolicy) -> bool:
    if illust.is_r18g:
        return policy.allow_r18g
    if illust.is_r18:
        return policy.allow_r18
    return True


def select_pages(illust: PixivIllust, max_pages: int) -> Tuple[List[PixivPage], bool]:
    limit = max(1, max_pages)
    selected = illust.pages[:limit]
    return selected, len(illust.pages) > limit


def should_use_forward(illust: PixivIllust) -> bool:
    return illust.page_count > 1 or illust.is_restricted


def build_info_text(illust: PixivIllust) -> str:
    return f"{illust.title}\n{illust.author}\npid: {illust.pid}"


def build_forward_contents(illust: PixivIllust, images: List[Any]) -> List[Any]:
    return [build_info_text(illust), *images]


def detect_image_ext(data: bytes, fallback: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    fallback = fallback.lower().lstrip(".")
    return fallback if fallback in {"jpg", "jpeg", "png", "gif", "webp"} else "jpg"


def describe_client_error(kind: str) -> str:
    descriptions = {
        "auth": "Pixiv 登录失败，请检查 refresh_token 配置",
        "not_found": "作品不存在、已删除或当前账号无权限查看",
        "forbidden": "当前账号无权限查看该作品，或代理/防盗链请求被 Pixiv 拒绝",
        "rate_limited": "Pixiv 请求过于频繁，请稍后再试",
        "timeout": "请求 Pixiv 超时，请检查网络或代理",
        "network": "连接 Pixiv 失败，请检查网络或代理",
        "too_large": "图片或动图文件过大，已停止发送",
        "ugoira": "动图处理失败，请稍后再试",
        "send_forward": "合并转发发送失败，可能是当前 OneBot/NapCat 不支持该图片内容或临时超时",
    }
    return descriptions.get(kind, "获取 Pixiv 图片失败，请稍后再试")
