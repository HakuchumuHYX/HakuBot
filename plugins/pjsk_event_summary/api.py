import httpx
from typing import List, Union
from .models import EventSimple, EventDetail

API_BASE = "https://sekaistoryadmin.exmeaning.com/api/v1/events"


async def fetch_event_list(limit: int = 20) -> List[EventSimple]:
    """
    获取活动列表
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(API_BASE)
        resp.raise_for_status()

        data_json = resp.json()
        # 解析数据
        events_all = [EventSimple(**item) for item in data_json]
        # 按ID倒序排列
        events_all.sort(key=lambda x: x.event_id, reverse=True)
        # 返回前 limit 条
        return events_all[:limit]


async def fetch_event_detail(event_id: str) -> Union[EventDetail, str]:
    """
    获取活动详情
    返回 EventDetail 对象，如果出错或找不到则返回错误信息字符串
    """
    target_url = f"{API_BASE}/{event_id}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(target_url)

            # API 即使 200 也可能返回 {"error": "..."}
            data_json = resp.json()
            if "error" in data_json:
                return f"查询失败: {data_json['error']}"

            resp.raise_for_status()

            return EventDetail(**data_json)
    except httpx.HTTPStatusError as e:
        return f"网络请求错误: HTTP {e.response.status_code}"
    except Exception as e:
        return f"发生未知错误: {str(e)}"