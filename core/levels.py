"""用户等级和命令等级：和插件开关、CD 是两套独立状态。3 级只来自 superusers 配置，不能授予

命令等级在代码里写默认值（level() 的参数），data/core/command_levels.json 里的覆写优先；
覆写只由 webconsole 经 webconsole_bridge 的接口修改。
"""

from dataclasses import dataclass
from enum import IntEnum

from nonebot import logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.permission import Permission

from core.access import is_superuser, load_state_file
from utils.json_io import atomic_write_json
from utils.paths import project_root

LEVELS_FILE = project_root() / "data/core/user_levels.json"
COMMAND_LEVELS_FILE = project_root() / "data/core/command_levels.json"


class Level(IntEnum):
    BLACKLISTED = 0
    USER = 1
    ADVANCED = 2
    SUPERUSER = 3


# 只记 SU 手动设置过的人：0 是全局黑名单，1、2 按群记 {群号: {QQ号: 等级}}
blacklist: set[str] = set()
group_levels: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class CommandSpec:
    id: str
    default: Level


# level() 导入时登记，是全部命令的清单；覆写 {命令ID: 等级} 只记和默认值不同的
commands: dict[str, CommandSpec] = {}
command_overrides: dict[str, int] = {}


def load() -> None:
    """启动时由 bootstrap 显式调用"""
    data = load_state_file(LEVELS_FILE)
    blacklist.clear()
    blacklist.update(data.get("blacklist", []))
    group_levels.clear()
    group_levels.update(data.get("groups", {}))
    command_overrides.clear()
    command_overrides.update(load_state_file(COMMAND_LEVELS_FILE))


def _save() -> None:
    data = {"blacklist": sorted(blacklist), "groups": group_levels}
    atomic_write_json(LEVELS_FILE, data, indent=2, ensure_ascii=False)


def resolve_level(
    user_id: str, group_id: str | None, role: str | None
) -> tuple[Level, str]:
    """返回等级和来源。SU 手动设的优先于群身份；私聊不看群设置和群身份"""
    if is_superuser(user_id):
        return Level.SUPERUSER, "超级用户配置"
    if user_id in blacklist:
        return Level.BLACKLISTED, "全局黑名单"
    if group_id is None:
        return Level.USER, "默认"
    manual = group_levels.get(group_id, {}).get(user_id)
    if manual is not None:
        return Level(manual), "本群手动设置"
    if role == "owner":
        return Level.ADVANCED, "群主"
    if role == "admin":
        return Level.ADVANCED, "群管理"
    return Level.USER, "默认"


def user_level(event: Event) -> Level:
    """通知事件没有 sender.role，认不出群管理，只看手动设置"""
    # 通知事件不一定有 user_id，get_user_id() 会抛
    user_id = str(getattr(event, "user_id", ""))
    group_id = getattr(event, "group_id", None)
    role = event.sender.role if isinstance(event, GroupMessageEvent) else None
    group = None if group_id is None else str(group_id)
    return resolve_level(user_id, group, role)[0]


def level(command_id: str, default: Level) -> Permission:
    """挂在 matcher 的 permission= 上；不够级不匹配、不回复。ID 写成「插件名:命令词」"""
    if command_id in commands:
        raise ValueError(f"命令 ID 重复: {command_id}")
    commands[command_id] = CommandSpec(command_id, default)

    async def _check(event: Event) -> bool:
        # 每次现查，改了覆写立刻生效
        return user_level(event) >= required_level(command_id)

    return Permission(_check)


def required_level(command_id: str) -> Level:
    override = command_overrides.get(command_id)
    return commands[command_id].default if override is None else Level(override)


def set_command_level(command_id: str, value: Level) -> None:
    """和默认值相同就删掉覆写，文件只留真正改过的"""
    if value == commands[command_id].default:
        command_overrides.pop(command_id, None)
    else:
        command_overrides[command_id] = int(value)
    atomic_write_json(
        COMMAND_LEVELS_FILE, command_overrides, indent=2, ensure_ascii=False
    )


def warn_unknown_overrides() -> None:
    """全部插件加载完后调用：代码里删掉的命令、加载失败的插件，覆写里会留下未登记的 ID"""
    unknown = sorted(set(command_overrides) - set(commands))
    if unknown:
        logger.warning(f"命令等级覆写里有未登记的 ID，已忽略: {', '.join(unknown)}")


def set_level(user_id: str, group_id: str | None, value: Level) -> None:
    """0 加入全局黑名单；1、2 写本群设置，并先移出黑名单，否则设了也不生效"""
    if value == Level.BLACKLISTED:
        blacklist.add(user_id)
    else:
        blacklist.discard(user_id)
        group_levels.setdefault(group_id, {})[user_id] = int(value)
    _save()


def reset_level(user_id: str, group_id: str | None) -> None:
    """撤销手动设置：移出黑名单；在群里还删除本群设置"""
    blacklist.discard(user_id)
    if group_id is not None:
        group = group_levels.get(group_id, {})
        group.pop(user_id, None)
        if not group:
            group_levels.pop(group_id, None)
    _save()
