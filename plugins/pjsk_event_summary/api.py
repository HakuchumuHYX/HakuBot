import json
from typing import List, Union

from ..utils.moesekai_hub import ensure_event_detail_path, ensure_event_index_ready, load_event_index
from ..utils.tools import get_exc_desc, get_logger
from .models import EventSimple, EventDetail

logger = get_logger("pjsk_event_summary.api")


async def fetch_event_list(limit: int = 20) -> List[EventSimple]:
    """
    获取活动列表
    """
    await ensure_event_index_ready()
    data_json = load_event_index()
    return [EventSimple(**item) for item in data_json[:limit]]


async def fetch_event_detail(event_id: str) -> Union[EventDetail, str]:
    """
    获取活动详情
    返回 EventDetail 对象，如果出错或找不到则返回错误信息字符串
    """
    try:
        event_id_int = int(event_id)
        local_path = await ensure_event_detail_path(event_id_int)
        if local_path is None:
            return f"未收录活动 {event_id} 的剧情总结"

        with local_path.open("r", encoding="utf-8") as f:
            data_json = json.load(f)
        return EventDetail(**data_json)
    except Exception as e:
        logger.exception(f"读取活动 {event_id} 详情失败: {e}")
        return f"读取本地剧情数据失败: {get_exc_desc(e)}"
