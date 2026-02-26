"""HLTV 图片渲染模块"""

from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import Optional
import pytz

from ..utils.browser import template_to_pic

from .config import plugin_config
from .data_source import EventInfo, MatchInfo, ResultInfo, MatchStats

# 导入 UpcomingMatch 类型（用于类型提示）
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .scheduler import UpcomingMatch


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
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text
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
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text
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
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text
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
        stats_dict = asdict(stats)
    
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="stats.html",
        templates={
            "stats": stats_dict,
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text
        },
        pages={
            "viewport": {"width": 800, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )


async def render_help(sections: list[dict]) -> bytes:
    """渲染帮助图片

    Args:
        sections: 帮助分组数据，格式：
          [
            {
              "title": str,
              "note": str,
              "commands": [
                {
                  "name": str,
                  "args": str,
                  "aliases": list[str],
                  "admin_only": bool,
                  "superuser_only": bool
                }
              ]
            }
          ]
    """
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="help.html",
        templates={
            "sections": sections,
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text,
        },
        pages={
            "viewport": {"width": 820, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/",
        },
    )


async def render_reminder(
    team1: str,
    team2: str,
    event_title: str,
    minutes_until: int,
    start_time_str: str = "",
    maps: str = ""
) -> bytes:
    """渲染比赛开始提醒图片
    
    Args:
        team1: 队伍1名称
        team2: 队伍2名称
        event_title: 赛事名称
        minutes_until: 距离开始的分钟数
        start_time_str: 开始时间字符串
        maps: 比赛格式（如 "3" 表示 BO3）
    
    Returns:
        渲染后的图片字节
    """
    return await template_to_pic(
        template_path=str(TEMPLATE_DIR),
        template_name="reminder.html",
        templates={
            "team1": team1,
            "team2": team2,
            "event_title": event_title,
            "minutes_until": minutes_until,
            "start_time_str": start_time_str,
            "maps": maps,
            "timestamp": get_timestamp(),
            "watermark_text": plugin_config.hltv_watermark_text
        },
        pages={
            "viewport": {"width": 550, "height": 100},
            "base_url": f"file://{TEMPLATE_DIR}/"
        }
    )
