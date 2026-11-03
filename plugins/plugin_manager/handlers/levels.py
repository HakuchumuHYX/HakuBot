from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.exception import ActionFailed
from nonebot.params import CommandArg
from utils.logging import get_exc_desc
from utils.onebot.messages import mentioned_users
from core.access import is_superuser
from core.levels import (
    Level,
    blacklist,
    group_levels,
    level,
    reset_level,
    resolve_level,
    set_level,
)

set_level_cmd = on_command(
    "设置等级",
    permission=level("plugin_manager:设置等级", Level.SUPERUSER),
    priority=5,
    block=True,
)

reset_level_cmd = on_command(
    "重置等级",
    permission=level("plugin_manager:重置等级", Level.SUPERUSER),
    priority=5,
    block=True,
)

show_level_cmd = on_command(
    "查看等级",
    permission=level("plugin_manager:查看等级", Level.SUPERUSER),
    priority=5,
    block=True,
)


def group_of(event: MessageEvent) -> str | None:
    return str(event.group_id) if isinstance(event, GroupMessageEvent) else None


def parse_target(args: Message) -> tuple[str | None, list[str]]:
    """目标写 @ 或 QQ 号；返回目标和其余文字参数"""
    words = args.extract_plain_text().split()
    if mentioned := mentioned_users(args):
        return str(mentioned[0]), words
    if words and words[0].isdecimal():
        return words[0], words[1:]
    return None, words


@set_level_cmd.handle()
async def handle_set_level(event: MessageEvent, args: Message = CommandArg()):
    user_id, rest = parse_target(args)
    if user_id is None or len(rest) != 1 or rest[0] not in ("0", "1", "2"):
        await set_level_cmd.finish(
            "用法：设置等级 @某人或QQ号 等级（0 为全局拉黑，1、2 只对本群生效）"
        )
    if is_superuser(user_id):
        await set_level_cmd.finish("超级用户的等级固定为 3")

    value = Level(int(rest[0]))
    group_id = group_of(event)
    if value == Level.BLACKLISTED:
        set_level(user_id, group_id, value)
        await set_level_cmd.finish(f"已拉黑 {user_id}，所有群生效")
    if group_id is None:
        await set_level_cmd.finish("1、2 级只对本群生效，请在群聊中设置")

    was_blacklisted = user_id in blacklist
    set_level(user_id, group_id, value)
    suffix = "，并已解除拉黑" if was_blacklisted else ""
    await set_level_cmd.finish(f"已将 {user_id} 在本群设为 {value} 级{suffix}")


@reset_level_cmd.handle()
async def handle_reset_level(event: MessageEvent, args: Message = CommandArg()):
    user_id, rest = parse_target(args)
    if user_id is None or rest:
        await reset_level_cmd.finish("用法：重置等级 @某人或QQ号")

    group_id = group_of(event)
    reset_level(user_id, group_id)
    if group_id is None:
        await reset_level_cmd.finish(f"已将 {user_id} 移出黑名单")
    await reset_level_cmd.finish(
        f"已撤销 {user_id} 的手动设置（黑名单和本群设置），回到按群身份计算"
    )


@show_level_cmd.handle()
async def handle_show_level(
    bot: Bot, event: MessageEvent, args: Message = CommandArg()
):
    user_id, rest = parse_target(args)
    if rest:
        await show_level_cmd.finish("用法：查看等级 [@某人或QQ号]")
    group_id = group_of(event)
    if user_id is None:
        await show_level_cmd.finish(overview(group_id))

    role = None
    if group_id is not None:
        try:
            member = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(user_id)
            )
            role = member["role"]
        except ActionFailed as e:
            # 不在本群时也会走到这里
            logger.warning(f"查询群成员身份失败，按非管理计算: {get_exc_desc(e)}")

    value, source = resolve_level(user_id, group_id, role)
    where = "在本群" if group_id is not None else "在私聊"
    await show_level_cmd.finish(f"{user_id} {where}：{value} 级（{source}）")


def overview(group_id: str | None) -> str:
    lines = []
    if group_id is not None:
        lines.append("本群手动设置：")
        manual = group_levels.get(group_id, {})
        for user_id, value in sorted(manual.items()):
            note = "（已拉黑，暂不生效）" if user_id in blacklist else ""
            lines.append(f"  {user_id} → {value} 级{note}")
        if not manual:
            lines.append("  无")
    lines.append("全局黑名单：")
    lines.extend(f"  {user_id}" for user_id in sorted(blacklist))
    if not blacklist:
        lines.append("  无")
    return "\n".join(lines)
