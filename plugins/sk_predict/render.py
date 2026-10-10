from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from utils.rendering.engine import render_template

CST = timezone(timedelta(hours=8))
TEMPLATE_DIR = Path(__file__).parent / "templates"
EVENT_TYPES = {
    "marathon": "马拉松",
    "cheerful_carnival": "欢乐嘉年华",
    "world_bloom": "World Link",
}


def format_number(value: Optional[int]) -> str:
    if value is None:
        return "--"
    return f"{int(value):,}"


def format_delta(current: Optional[int], prediction: Optional[int]) -> str:
    if current is None or prediction is None:
        return "--"
    delta = int(prediction) - int(current)
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{abs(delta):,}"


def progress(current: Optional[int], prediction: Optional[int]) -> Optional[float]:
    if not current or not prediction:
        return None
    return min(100.0, current / prediction * 100)


def parse_event_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CST)


async def render_prediction_card(
    event_info: dict[str, Any], latest_data: dict[str, Any]
) -> bytes:
    start_at = parse_event_ms(int(event_info["start_at"]))
    end_at = parse_event_ms(int(event_info["end_at"]))
    updated_at = datetime.fromisoformat(
        latest_data["updated_at"].replace("Z", "+00:00")
    )

    rows = [
        {
            "rank": f"T{item['rank']}" if item.get("rank") is not None else "--",
            "score": format_number(item.get("score")),
            "prediction": format_number(item.get("prediction")),
            "delta": format_delta(item.get("score"), item.get("prediction")),
            "progress": progress(item.get("score"), item.get("prediction")),
        }
        for item in latest_data.get("items") or []
    ]

    return await render_template(
        TEMPLATE_DIR,
        "prediction.html",
        width=920,
        event_id=event_info["event_id"],
        name=event_info["name"],
        event_type=EVENT_TYPES.get(event_info.get("event_type"), ""),
        period=f"{start_at:%m-%d %H:%M} ~ {end_at:%m-%d %H:%M}",
        updated_at=f"{updated_at.astimezone(CST):%m-%d %H:%M:%S}",
        rows=rows,
        status=latest_data.get("status", "unknown"),
    )
