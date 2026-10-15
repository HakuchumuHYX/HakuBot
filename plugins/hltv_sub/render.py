"""HLTV 图片渲染模块"""

import re
from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import Optional
import pytz
from nonebot import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from utils.rendering.engine import render_template

from plugins.hltv_sub.config import plugin_config
from plugins.hltv_sub.models import EventInfo, EventMvp, MatchInfo, ResultInfo, MatchStats

# 模板目录
TEMPLATE_DIR = Path(__file__).parent / "templates"
# 队标、国旗、地图横幅都从 HLTV 远程加载；等不到就改渲染无图版本，不退回文字。
IMAGE_TIMEOUT_MS = 15_000

VETO_PATTERN = re.compile(r"^\d+\.\s*(.+?)\s+(removed|picked)\s+(.+)$")
LEFTOVER_PATTERN = re.compile(r"^\d+\.\s*(.+?)\s+was left over$")


async def _render(template_name: str, context: dict, width: int) -> bytes:
    tz = pytz.timezone(plugin_config.hltv_timezone)
    context = {
        **context,
        "timestamp": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "watermark_text": plugin_config.hltv_watermark_text,
    }
    try:
        return await render_template(
            TEMPLATE_DIR, template_name, width=width,
            timeout=IMAGE_TIMEOUT_MS, show_images=True, **context,
        )
    except PlaywrightTimeoutError:
        logger.warning(f"[HLTV] {template_name} 远程图片加载超时，改为渲染无图版本")
        return await render_template(
            TEMPLATE_DIR, template_name, width=width, show_images=False, **context
        )


def _veto_steps(stats: MatchStats) -> list[dict]:
    """把 "3. Spirit picked Dust2" 拆成结构化步骤；认不出的行原样保留。"""
    sides = {stats.team1.lower(): "team1", stats.team2.lower(): "team2"}
    steps = []
    for line in stats.vetos:
        if match := VETO_PATTERN.match(line):
            team, action, map_name = match.groups()
            steps.append({
                "action": "pick" if action == "picked" else "ban",
                "side": sides.get(team.lower(), ""),
                "map": map_name,
            })
        elif match := LEFTOVER_PATTERN.match(line):
            steps.append({"action": "left", "side": "", "map": match.group(1)})
        else:
            steps.append({"action": "text", "side": "", "map": line})
    return steps


async def render_events(
    ongoing_events: list[EventInfo],
    upcoming_events: list[EventInfo],
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

    results_dict = {
        event_name: [{**asdict(r), "date": r.date[5:]} for r in results]
        for event_name, results in results_by_event.items()
    }

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
        stats_dict["veto_steps"] = _veto_steps(stats)

    return await _render(
        "stats.html",
        {
            "stats": stats_dict,
        },
        width=1040,
    )


async def render_reminder(match: MatchInfo, event_title: str) -> bytes:
    """渲染比赛已开始的提醒。"""
    return await _render(
        "reminder.html",
        {"match": asdict(match), "event_title": event_title},
        width=560,
    )


async def render_mvp(event_title: str, mvp: EventMvp) -> bytes:
    """渲染赛事 MVP 播报。"""
    return await _render(
        "mvp.html",
        {"mvp": asdict(mvp), "event_title": event_title},
        width=640,
    )
