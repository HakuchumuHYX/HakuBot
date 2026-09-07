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
    """格式化 Unix 时间戳（毫秒）为日期字符串 (MM-DD)"""
    try:
        if unix_timestamp:
            ts = int(unix_timestamp) / 1000
            dt = datetime.fromtimestamp(ts, tz)
            return dt.strftime("%m-%d")
    except Exception:
        pass
    return ""


def format_time(unix_timestamp: str, tz: pytz.BaseTzInfo) -> str:
    """格式化 Unix 时间戳（毫秒）为时间字符串 (HH:MM)"""
    try:
        if unix_timestamp:
            ts = int(unix_timestamp) / 1000
            dt = datetime.fromtimestamp(ts, tz)
            return dt.strftime("%H:%M")
    except Exception:
        pass
    return ""
