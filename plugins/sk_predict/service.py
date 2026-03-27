from __future__ import annotations

import time
from pathlib import Path

from .config import CACHE_TTL_SECONDS, plugin_config
from .data_source import fetch_prediction_payload
from .render import render_prediction_card


def read_cache_age_text(image_path: Path) -> str:
    minutes_ago = int((time.time() - image_path.stat().st_mtime) / 60)
    if minutes_ago < 1:
        return "刚刚"
    return f"{minutes_ago}分钟前"


def get_cache_file(region: str) -> Path:
    return plugin_config.get_cache_file(region)


def is_cache_valid(region: str, *, force_reload: bool) -> bool:
    cache_file = get_cache_file(region)
    if force_reload:
        return False
    if not cache_file.exists():
        return False
    return time.time() - cache_file.stat().st_mtime <= CACHE_TTL_SECONDS


async def generate_prediction_image(region: str) -> bytes:
    event_info, latest_data = await fetch_prediction_payload(region)
    return await render_prediction_card(event_info, latest_data)


async def refresh_prediction_cache(region: str) -> bytes:
    cache_file = get_cache_file(region)
    img_bytes = await generate_prediction_image(region)
    cache_file.write_bytes(img_bytes)
    return img_bytes
