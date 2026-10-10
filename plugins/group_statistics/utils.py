from typing import List, Tuple

from plugins.group_statistics.data_manager import data_manager
from plugins.group_statistics.config import (
    TOP_N_USERS,
    MESSAGE_THRESHOLDS,
    DEFAULT_THRESHOLD_TEXT,
)
from utils.logging import get_logger

logger = get_logger("group_statistics.utils")


def get_top_users(
    group_id: int, top_n: int = TOP_N_USERS
) -> List[Tuple[str, str, int]]:
    """获取指定群组的发言排名前N的用户"""
    if group_id not in data_manager.group_stats:
        return []

    # 按消息数量排序
    sorted_users = sorted(
        data_manager.group_stats[group_id].items(), key=lambda x: x[1], reverse=True
    )[:top_n]

    # 转换为(QQ 号, 群名片, 数量)格式，QQ 号用来取头像
    result = []
    for user_id, count in sorted_users:
        card = data_manager.user_info[group_id].get(user_id, f"用户{user_id}")
        result.append((user_id, card, count))

    return result


def get_total_messages(group_id: int) -> int:
    """获取指定群组的总消息数"""
    if group_id not in data_manager.group_stats:
        return 0
    return sum(data_manager.group_stats[group_id].values())


def get_additional_text(total: int) -> str:
    """根据总消息数量获取额外的文本"""
    for threshold, text in sorted(MESSAGE_THRESHOLDS.items(), reverse=True):
        if total >= threshold:
            return text
    return DEFAULT_THRESHOLD_TEXT


def reset_daily_stats():
    """重置每日统计数据"""
    data_manager.group_stats = {}
    data_manager.user_info = {}
    data_manager.save_stats()
    logger.info("已重置每日统计数据")
