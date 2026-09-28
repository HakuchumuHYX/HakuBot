"""HLTV 图片渲染模块"""

from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import Optional
import pytz

from utils.rendering.engine import render_template_image

from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.models import EventInfo, MatchInfo, ResultInfo, MatchStats

# 模板目录
TEMPLATE_DIR = Path(__file__).parent / "templates"


async def _render(template_name: str, context: dict, width: int) -> bytes:
    tz = pytz.timezone(plugin_config.hltv_timezone)
    return await render_template_image(
        template_path=str(TEMPLATE_DIR),
        template_name=template_name,
        templates={
            **context,
            "timestamp": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
            "watermark_text": plugin_config.hltv_watermark_text,
        },
        pages={
            "viewport": {"width": width, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/",
        },
    )


async def render_events(
    ongoing_events: list[EventInfo],
    upcoming_events: list[EventInfo],
    subscribed_ids: list[str],
) -> bytes:
    """渲染赛事列表图片"""

    # 转换为字典以便在模板中使用
    ongoing = [
        {**asdict(e), "start_date": e.start_date[5:], "end_date": e.end_date[5:]}
        for e in ongoing_events
    ]
    upcoming = [
        {**asdict(e), "start_date": e.start_date[5:], "end_date": e.end_date[5:]}
        for e in upcoming_events
    ]

    return await _render(
        "events.html",
        {
            "ongoing_events": ongoing,
            "upcoming_events": upcoming,
            "subscribed_ids": subscribed_ids,
        },
        width=650,
    )


async def render_matches(
    matches_by_event: dict[str, list[MatchInfo]], live_count: int, upcoming_count: int
) -> bytes:
    """渲染比赛列表图片"""

    # 转换数据结构
    matches_dict = {}
    for event_name, matches in matches_by_event.items():
        matches_dict[event_name] = [
            {
                **asdict(m),
                "date": m.start_time.strftime("%m-%d") if m.start_time else "",
                "time": m.start_time.strftime("%H:%M") if m.start_time else "",
            }
            for m in matches
        ]

    return await _render(
        "matches.html",
        {
            "matches_by_event": matches_dict,
            "live_count": live_count,
            "upcoming_count": upcoming_count,
        },
        width=700,
    )


async def render_results(results_by_event: dict[str, list[ResultInfo]]) -> bytes:
    """渲染结果列表图片"""

    # 转换数据结构
    results_dict = {}
    for event_name, results in results_by_event.items():
        results_dict[event_name] = [asdict(r) for r in results]

    return await _render(
        "results.html",
        {
            "results_by_event": results_dict,
        },
        width=700,
    )


async def render_stats(stats: Optional[MatchStats]) -> bytes:
    """渲染比赛数据图片"""

    stats_dict = None
    if stats:
        stats_dict = asdict(stats)
        played_maps = [
            m for m in stats.maps if m.score_team1 != "-" and m.score_team2 != "-"
        ]
        has_single_map_details = (
            len(played_maps) == 1 and played_maps[0].map_name in stats.map_stats_details
        )
        stats_dict["show_total_overview"] = not has_single_map_details

    return await _render(
        "stats.html",
        {
            "stats": stats_dict,
        },
        width=800,
    )


async def render_reminder(
    team1: str,
    team2: str,
    event_title: str,
    maps: str = "",
    is_grand_final: bool = False,
    is_third_place: bool = False,
) -> bytes:
    """渲染比赛已开始的提醒。"""
    return await _render(
        "reminder.html",
        {
            "team1": team1,
            "team2": team2,
            "event_title": event_title,
            "maps": maps,
            "is_grand_final": is_grand_final,
            "is_third_place": is_third_place,
        },
        width=550,
    )
