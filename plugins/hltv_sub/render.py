"""HLTV 图片渲染模块"""

from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import Optional
import pytz

from nonebot_plugin_htmlrender import template_to_pic

from .config import plugin_config
from .data_source import EventInfo, MatchInfo, ResultInfo, MatchStats


# 模板目录
TEMPLATE_DIR = Path(__file__).parent / "templates"


def get_timestamp() -> str:
    """获取当前时间戳"""
    tz = pytz.timezone(plugin_config.hltv_timezone)
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


async def render_events(
    ongoing_events: list[EventInfo],
    upcoming_events: list[EventInfo],
    subscribed_ids: list[str]
) -> bytes:
    """渲染赛事列表图片"""
    
    # 转换为字典以便在模板中使用
    ongoing = [asdict(e) for e in ongoing_events]
    upcoming = [asdict(e) for e in upcoming_events]
    
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="events.html",
        templates={
            "ongoing_events": ongoing,
            "upcoming_events": upcoming,
            "subscribed_ids": subscribed_ids,
            "timestamp": get_timestamp()
        },
        pages={
            "viewport": {"width": 650, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )


async def render_matches(
    matches_by_event: dict[str, list[MatchInfo]],
    live_count: int,
    upcoming_count: int
) -> bytes:
    """渲染比赛列表图片"""
    
    # 转换数据结构
    matches_dict = {}
    for event_name, matches in matches_by_event.items():
        matches_dict[event_name] = [asdict(m) for m in matches]
    
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="matches.html",
        templates={
            "matches_by_event": matches_dict,
            "live_count": live_count,
            "upcoming_count": upcoming_count,
            "timestamp": get_timestamp()
        },
        pages={
            "viewport": {"width": 700, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )


async def render_results(
    results_by_event: dict[str, list[ResultInfo]]
) -> bytes:
    """渲染结果列表图片"""
    
    # 转换数据结构
    results_dict = {}
    for event_name, results in results_by_event.items():
        results_dict[event_name] = [asdict(r) for r in results]
    
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="results.html",
        templates={
            "results_by_event": results_dict,
            "timestamp": get_timestamp()
        },
        pages={
            "viewport": {"width": 700, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )


async def render_stats(stats: Optional[MatchStats]) -> bytes:
    """渲染比赛数据图片"""
    
    stats_dict = None
    if stats:
        stats_dict = {
            "match_id": stats.match_id,
            "team1": stats.team1,
            "team2": stats.team2,
            "score1": stats.score1,
            "score2": stats.score2,
            "status": stats.status,
            "event": stats.event,
            "maps": [asdict(m) for m in stats.maps],
            "players": [asdict(p) for p in stats.players]
        }
    
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="stats.html",
        templates={
            "stats": stats_dict,
            "timestamp": get_timestamp()
        },
        pages={
            "viewport": {"width": 800, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )
