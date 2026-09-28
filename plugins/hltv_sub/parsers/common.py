"""
HLTV 解析公共工具函数（纯函数）
"""

from __future__ import annotations

import re
from datetime import datetime

import pytz


def extract_id_from_url(url: str) -> str:
    """从 URL 中提取数字 ID（匹配 /12345/）"""
    match = re.search(r"/(\d+)/", url or "")
    return match.group(1) if match else ""


def format_date(unix_timestamp: str, tz: pytz.BaseTzInfo) -> str:
    """保留年份；月日展示由渲染层负责。"""
    try:
        if unix_timestamp:
            ts = int(unix_timestamp) / 1000
            dt = datetime.fromtimestamp(ts, tz)
            return dt.date().isoformat()
    except Exception:
        pass
    return ""


def parse_start_time(unix_timestamp: str, tz: pytz.BaseTzInfo) -> datetime | None:
    try:
        if unix_timestamp:
            ts = int(unix_timestamp) / 1000
            return datetime.fromtimestamp(ts, tz)
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None
