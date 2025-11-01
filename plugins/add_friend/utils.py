import re
from typing import Optional
from .config import GROUP_PATTERNS


async def extract_group_from_comment(comment: str) -> Optional[str]:
    """
    从验证信息中提取群号

    Args:
        comment: 好友请求的验证信息

    Returns:
        提取到的群号，如果未找到则返回None
    """
    if not comment:
        return None

    # 尝试多种模式匹配群号
    for pattern in GROUP_PATTERNS:
        match = re.search(pattern, comment, re.IGNORECASE)
        if match:
            # 如果是最后一个模式（纯数字），需要确保不是QQ号
            if pattern == r'(\d+)':
                # 简单检查：如果数字长度在4-10位之间，可能是群号
                number = match.group(1)
                if 4 <= len(number) <= 10:
                    return number
            else:
                return match.group(1)

    return None


def create_request_data(user_id: int, comment: str, group: Optional[str], flag: str) -> dict:
    """创建好友请求数据字典"""
    return {
        "user_id": user_id,
        "comment": comment,
        "group": group,
        "flag": flag
    }