from __future__ import annotations

from typing import Any

from ..utils.network import HttpError, get_client_session
from .config import plugin_config


class PredictApiError(RuntimeError):
    pass


async def fetch_json(url: str) -> Any:
    async with get_client_session().get(url, verify_ssl=False) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise HttpError(resp.status, text or resp.reason)
        return await resp.json(content_type=None)


async def fetch_active_event(region: str) -> dict[str, Any]:
    data = await fetch_json(plugin_config.get_events_url(region))
    if not isinstance(data, list):
        raise PredictApiError("活动列表返回格式异常")

    for item in data:
        if item.get("status") == "active" and item.get("has_realtime_data"):
            return item

    for item in data:
        if item.get("status") == "active":
            return item

    raise PredictApiError("当前没有进行中的活动")


async def fetch_latest_prediction(event_id: int, region: str) -> dict[str, Any]:
    data = await fetch_json(plugin_config.get_latest_url_template(region).format(event_id=event_id))
    if not isinstance(data, dict) or "items" not in data:
        raise PredictApiError("预测接口返回格式异常")
    return data


async def fetch_prediction_payload(region: str) -> tuple[dict[str, Any], dict[str, Any]]:
    event_info = await fetch_active_event(region)
    latest_data = await fetch_latest_prediction(int(event_info["event_id"]), region)
    return event_info, latest_data
