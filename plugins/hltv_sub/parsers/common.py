"""
HLTV 解析公共工具函数（纯函数）
"""

from __future__ import annotations

import re
from datetime import datetime

import pytz
from nonebot import logger


def text_of(elem) -> str:
    """可选元素缺失时取空串。"""
    return elem.get_text(strip=True) if elem else ""


def extract_id_from_url(url: str) -> str:
    """从 URL 中提取数字 ID（匹配 /12345/）"""
    match = re.search(r"/(\d+)/", url or "")
    return match.group(1) if match else ""


def absolute_url(src: str) -> str:
    # 站内相对路径都是 /img/static 下的静态图；www 时常被 Cloudflare 挑战，resources 是同源静态域名。
    return f"https://resources.hltv.org{src}" if src.startswith("/") else src


def is_grand_final_text(text: str) -> bool:
    """matches 页的阶段标签和 match 页的赛制说明都用这一条规则认决赛。"""
    return "grand final" in " ".join((text or "").lower().split())


THIRD_PLACE_MARKERS = (
    "3rd place decider",
    "third place decider",
    "3rd place match",
    "third place match",
)


def is_third_place_text(text: str) -> bool:
    """matches 页的卡片说明和 match 页的赛段共用。"""
    text = " ".join((text or "").lower().split())
    return any(marker in text for marker in THIRD_PLACE_MARKERS)


def pick_logo(imgs) -> str:
    """模板是深色底，有 night-only 变体时优先用它。"""
    imgs = list(imgs)
    if not imgs:
        return ""
    img = next((i for i in imgs if "night-only" in (i.get("class") or [])), imgs[0])
    # 截图按 2 倍缩放，srcset 里的 2x 图更清晰。
    srcset = img.get("srcset", "")
    return absolute_url(srcset.split()[0] if srcset else img.get("src", ""))


def flag_of(img) -> tuple[str, str]:
    """从 /img/static/flags/30x20/SE.gif 取旗帜代码；模板按代码拼高清图。"""
    if img is None:
        return "", ""
    match = re.search(r"/flags/[^/]+/([A-Za-z]+)\.", img.get("src", ""))
    return (match.group(1) if match else ""), img.get("alt", "")


def format_date(unix_timestamp: str, tz: pytz.BaseTzInfo) -> str:
    """保留年份；月日展示由渲染层负责。"""
    dt = parse_start_time(unix_timestamp, tz)
    return dt.date().isoformat() if dt else ""


def parse_start_time(unix_timestamp: str, tz: pytz.BaseTzInfo) -> datetime | None:
    """页面上的毫秒时间戳；待定的比赛没有这个属性。"""
    if not unix_timestamp:
        return None
    try:
        return datetime.fromtimestamp(int(unix_timestamp) / 1000, tz)
    except ValueError:
        logger.warning(f"[HLTV] 时间戳不是数字: {unix_timestamp!r}")
        return None
