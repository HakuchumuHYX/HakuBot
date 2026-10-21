from nonebot import get_driver
from nonebot.adapters import Event
from nonebot.rule import Rule

from core.access_state import plugin_status, save_plugin_status
from core.lifecycle import runtime
from core.registry import FEATURES_BY_ID


def is_superuser(user_id: str) -> bool:
    return user_id in get_driver().config.superusers


def _parent(feature_id: str) -> str:
    return feature_id.split(":", 1)[0]


def is_unavailable(feature_id: str) -> bool:
    """功能自己或主插件的启动钩子失败过"""
    return any(
        runtime.health.get(key, "").startswith("unavailable:")
        for key in (feature_id, _parent(feature_id))
    )


def _switched_on(feature_id: str, group_id: str) -> bool:
    override = plugin_status.get(feature_id, {}).get(group_id)
    return FEATURES_BY_ID[feature_id].default if override is None else override


def is_enabled(feature_id: str, group_id: str) -> bool:
    """本群开没开，不看触发者。子功能要求主插件也开着。"""
    if is_unavailable(feature_id):
        return False
    parent = _parent(feature_id)
    if parent != feature_id and not _switched_on(parent, group_id):
        return False
    return _switched_on(feature_id, group_id)


def switch_rule(feature_id: str, *, superuser_bypass: bool = True) -> Rule:
    """挂在 matcher 上的群开关，私聊不限。

    旁听类（复读、统计计数、消息记录、入群欢迎）传 superuser_bypass=False，
    否则关了的群照样会处理超管的消息。启动失败对超管也拦。
    """
    if feature_id not in FEATURES_BY_ID:
        raise KeyError(f"未登记的功能: {feature_id}")

    async def _check(event: Event) -> bool:
        if is_unavailable(feature_id):
            return False
        group_id = getattr(event, "group_id", None)
        if group_id is None:
            return True
        # 通知事件不一定有 user_id，get_user_id() 会抛
        if superuser_bypass and is_superuser(str(getattr(event, "user_id", ""))):
            return True
        return is_enabled(feature_id, str(group_id))

    return Rule(_check)


def set_enabled(feature_id: str, group_id: str, enabled: bool) -> None:
    plugin_status.setdefault(feature_id, {})[group_id] = enabled
    save_plugin_status(plugin_status)


def reset_group(group_id: str) -> None:
    """清空本群所有开关记录，各功能回到默认值"""
    for feature_id in list(plugin_status):
        plugin_status[feature_id].pop(group_id, None)
        if not plugin_status[feature_id]:
            del plugin_status[feature_id]
    save_plugin_status(plugin_status)
