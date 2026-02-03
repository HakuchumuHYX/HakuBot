"""
HLTV 权限与开关辅助函数
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot

from .data_manager import data_manager


def is_group_enabled(group_id: int) -> bool:
    """检查群组是否启用插件"""
    return data_manager.is_enabled(group_id)


async def check_permission(bot: Bot, group_id: int, user_id: int) -> bool:
    """检查权限：群主、管理员或超级用户"""
    superusers = getattr(bot.config, "superusers", set())
    if str(user_id) in superusers:
        return True

    try:
        member_info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return member_info.get("role") in ("owner", "admin")
    except Exception:
        return False
