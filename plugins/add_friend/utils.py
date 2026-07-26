import re
from typing import Optional, Set
from .config import GROUP_PATTERNS


def extract_group_candidates(comment: str) -> Set[str]:
    """
    从验证信息中提取所有可能的群号候选

    验证信息里可能同时出现多个数字（如“我是群123456的，QQ 987654321”），
    只取第一个命中容易误判，因此收集所有模式的所有命中，由调用方与白名单求交集判断。

    Args:
        comment: 好友请求的验证信息

    Returns:
        所有候选群号组成的集合，未找到则返回空集合
    """
    candidates: Set[str] = set()
    if not comment:
        return candidates

    # 尝试多种模式匹配群号，收集全部命中
    for pattern in GROUP_PATTERNS:
        for number in re.findall(pattern, comment, re.IGNORECASE):
            # 如果是最后一个模式（纯数字），需要确保不是QQ号
            # 简单检查：如果数字长度在4-10位之间，可能是群号
            if pattern == r'(\d+)' and not (4 <= len(number) <= 10):
                continue
            candidates.add(number)

    return candidates


async def extract_group_from_comment(comment: str) -> Optional[str]:
    """
    从验证信息中提取群号（基于候选集合，返回其中任意一个）

    Args:
        comment: 好友请求的验证信息

    Returns:
        提取到的群号，如果未找到则返回None
    """
    candidates = extract_group_candidates(comment)
    if not candidates:
        return None
    return next(iter(candidates))


def create_request_data(user_id: int, comment: str, group: Optional[str], flag: str) -> dict:
    """创建好友请求数据字典"""
    return {
        "user_id": user_id,
        "comment": comment,
        "group": group,
        "flag": flag
    }