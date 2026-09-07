from nonebot import get_driver
from typing import Optional
from utils.logging import get_logger
from core.registry import get_plugin_catalog
from core.access_state import (
    plugin_status,
    save_plugin_status,
)

logger = get_logger("access")


def get_status_override(status_key: str, group_id: str) -> Optional[bool]:
    """获取某个状态键在指定群的显式配置；未配置时返回 None。"""
    group_status = plugin_status.get(status_key)
    if not isinstance(group_status, dict):
        return None
    return group_status.get(group_id)


def get_plugin_feature_keys(plugin_name: str) -> list[str]:
    """从注册表查找主插件对应的所有子功能键。"""
    prefix = f"{plugin_name}:"
    return [
        plugin_id
        for plugin_id in get_plugin_catalog().keys()
        if plugin_id.startswith(prefix)
    ]


def sync_feature_statuses(plugin_name: str, group_id: str, enabled: bool):
    """同步指定主插件下所有子功能在当前群的状态。"""
    for feature_key in get_plugin_feature_keys(plugin_name):
        if feature_key not in plugin_status:
            plugin_status[feature_key] = {}
        plugin_status[feature_key][group_id] = enabled


def is_plugin_enabled(plugin_name: str, group_id: str, user_id: str) -> bool:
    """检查插件在指定群是否启用"""

    from core.lifecycle import runtime

    if runtime.health.get(plugin_name, "").startswith("unavailable:"):
        return False
    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True  # SuperUser无视开关
    except:
        pass

    # 默认启用
    if plugin_name not in plugin_status:
        return True
    if group_id not in plugin_status[plugin_name]:
        return True
    return plugin_status[plugin_name][group_id]


def set_plugin_status(plugin_name: str, group_id: str, enabled: bool):
    """设置插件状态"""
    if plugin_name not in plugin_status:
        plugin_status[plugin_name] = {}
    plugin_status[plugin_name][group_id] = enabled

    if ":" not in plugin_name:
        sync_feature_statuses(plugin_name, group_id, enabled)

    save_plugin_status(plugin_status)


def is_feature_enabled(
    plugin_name: str, feature_name: str, group_id: str, user_id: str
) -> bool:
    """检查插件的特定功能是否启用"""

    from core.lifecycle import runtime

    if runtime.health.get(f"{plugin_name}:{feature_name}", "").startswith(
        "unavailable:"
    ):
        return False

    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True  # SuperUser无视开关
    except:
        pass

    feature_key = f"{plugin_name}:{feature_name}"
    feature_override = get_status_override(feature_key, group_id)

    # 主插件禁用时，子功能一律禁用，不允许单独启用。
    if not is_plugin_enabled(plugin_name, group_id, user_id):
        return False

    # 主插件启用时，子功能默认启用，但允许显式关闭。
    if feature_override is False:
        return False
    return True


def set_feature_status(
    plugin_name: str, feature_name: str, group_id: str, enabled: bool
):
    """设置插件特定功能状态"""
    feature_key = f"{plugin_name}:{feature_name}"
    if feature_key not in plugin_status:
        plugin_status[feature_key] = {}
    plugin_status[feature_key][group_id] = enabled
    save_plugin_status(plugin_status)
