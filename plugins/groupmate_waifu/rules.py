"""
groupmate_waifu/rules.py
规则函数集中管理
"""

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from .constants import PLUGIN_NAME

# 导入外部插件管理 API
from ..plugin_manager.enable import is_plugin_enabled as _check_plugin
from ..plugin_manager.enable import is_feature_enabled as _check_feature


# --- 插件/功能启用检查（内部版本，同步） ---

def is_plugin_enabled(group_id: str, user_id: str) -> bool:
    """
    检查插件是否在指定群启用（同步版本）
    
    Args:
        group_id: 群号（字符串）
        user_id: 用户 QQ 号（字符串）
    
    Returns:
        是否启用
    """
    return _check_plugin(PLUGIN_NAME, group_id, user_id)


def is_yinpa_enabled(group_id: str, user_id: str) -> bool:
    """
    检查 yinpa 功能是否在指定群启用（同步版本）
    
    Args:
        group_id: 群号（字符串）
        user_id: 用户 QQ 号（字符串）
    
    Returns:
        是否启用
    """
    return _check_feature(PLUGIN_NAME, "yinpa", group_id, user_id)


def is_bye_enabled(group_id: str, user_id: str) -> bool:
    """
    检查 bye（离婚）功能是否在指定群启用（同步版本）
    
    Args:
        group_id: 群号（字符串）
        user_id: 用户 QQ 号（字符串）
    
    Returns:
        是否启用
    """
    return _check_feature(PLUGIN_NAME, "bye", group_id, user_id)


# --- 规则函数（异步版本，用于 matcher rule） ---

async def check_plugin_enabled(event: GroupMessageEvent) -> bool:
    """
    检查插件是否在当前群启用（异步版本，用于 matcher rule）
    """
    return is_plugin_enabled(str(event.group_id), str(event.user_id))


async def check_yinpa_enabled(event: GroupMessageEvent) -> bool:
    """
    检查 yinpa 功能是否在当前群启用（异步版本，用于 matcher rule）
    """
    return is_yinpa_enabled(str(event.group_id), str(event.user_id))


async def check_bye_enabled(event: GroupMessageEvent) -> bool:
    """
    检查 bye 功能是否在当前群启用（异步版本，用于 matcher rule）
    """
    return is_bye_enabled(str(event.group_id), str(event.user_id))
