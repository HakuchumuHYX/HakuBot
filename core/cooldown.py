import time
from typing import Optional

from nonebot.adapters import Event

from core.access import is_superuser
from core.access_state import cd_config, save_cd_config

# 上次使用时间只放内存：最长的 CD 也就几分钟，重启清零无所谓，不值得每次扣 CD 都重写文件
_last_used: dict[tuple[str, str, str], float] = {}


def get_cooldown(feature_id: str, group_id: str) -> int:
    """本群给这个功能设的 CD 秒数，没设为 0"""
    return cd_config.get(group_id, {}).get(feature_id, 0)


def _cooldown_key(feature_id: str, event: Event) -> Optional[tuple[str, str, str]]:
    """私聊和超管不受 CD 限制，返回 None"""
    group_id = getattr(event, "group_id", None)
    user_id = event.get_user_id()
    if group_id is None or is_superuser(user_id):
        return None
    return feature_id, str(group_id), user_id


def cooldown_remaining(feature_id: str, event: Event) -> int:
    """剩余冷却秒数，0 表示可以用"""
    key = _cooldown_key(feature_id, event)
    if key is None:
        return 0
    duration = get_cooldown(feature_id, key[1])
    passed = time.time() - _last_used.get(key, 0)
    return 0 if passed >= duration else int(duration - passed)


def mark_cooldown(feature_id: str, event: Event) -> None:
    """成功之后再调用：失败不扣 CD"""
    key = _cooldown_key(feature_id, event)
    if key is not None and get_cooldown(feature_id, key[1]) > 0:
        _last_used[key] = time.time()


def set_cooldown(feature_id: str, group_id: str, seconds: int) -> None:
    """seconds 为 0 时取消本群的 CD"""
    group_config = cd_config.setdefault(group_id, {})
    if seconds > 0:
        group_config[feature_id] = seconds
    else:
        group_config.pop(feature_id, None)
        if not group_config:
            del cd_config[group_id]
    save_cd_config(cd_config)
