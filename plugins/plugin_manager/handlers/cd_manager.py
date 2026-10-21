from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from utils.onebot.forward import send_forward_msg
from core.cooldown import get_cooldown, set_cooldown
from core.registry import FEATURES, FEATURES_BY_ID
from plugins.plugin_manager.handlers.enable import group_id_of

enable_cd = on_command("启用CD", permission=SUPERUSER, priority=5, block=True)

disable_cd = on_command("禁用CD", permission=SUPERUSER, priority=5, block=True)

list_cd = on_command("CD列表", permission=SUPERUSER, priority=5, block=True)


def cooldown_error(feature_id: str) -> str:
    """不能设 CD 时返回原因；只有代码里检查了 CD 的功能才能设"""
    spec = FEATURES_BY_ID.get(feature_id)
    if spec is None:
        return f"未登记此功能: {feature_id}"
    if not spec.cooldown:
        return f"{spec.name} ({feature_id}) 不支持 CD，可设置的见「CD列表」"
    return ""


@enable_cd.handle()
async def handle_enable_cd(event: MessageEvent, args: Message = CommandArg()):
    group_id = await group_id_of(enable_cd, event)
    parts = args.extract_plain_text().split()
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) <= 0:
        await enable_cd.finish(
            "用法：启用CD 插件名 秒数，子功能写成 插件名:功能名（仅对当前群生效）"
        )

    feature_id, seconds = parts[0], int(parts[1])
    if error := cooldown_error(feature_id):
        await enable_cd.finish(error)
    set_cooldown(feature_id, group_id, seconds)
    await enable_cd.finish(f"已在本群启用 {feature_id} 的 CD，时长 {seconds} 秒")


@disable_cd.handle()
async def handle_disable_cd(event: MessageEvent, args: Message = CommandArg()):
    group_id = await group_id_of(disable_cd, event)
    parts = args.extract_plain_text().split()
    if len(parts) != 1:
        await disable_cd.finish(
            "用法：禁用CD 插件名，子功能写成 插件名:功能名（仅对当前群生效）"
        )

    feature_id = parts[0]
    if error := cooldown_error(feature_id):
        await disable_cd.finish(error)
    set_cooldown(feature_id, group_id, 0)
    await disable_cd.finish(f"已在本群取消 {feature_id} 的 CD")


@list_cd.handle()
async def handle_list_cd(bot: Bot, event: MessageEvent):
    """列出本群可设 CD 的功能"""
    group_id = await group_id_of(list_cd, event)
    lines = []
    for item in FEATURES:
        if item.cooldown:
            seconds = get_cooldown(item.id, group_id)
            lines.append(
                f"{item.name} ({item.id}) - {f'{seconds} 秒' if seconds else '无CD'}"
            )
    message = "本群可设 CD 的功能:\n" + "\n".join(lines)

    try:
        await send_forward_msg(bot, event, items=[message])
    except Exception as e:
        logger.opt(exception=True).warning(f"合并转发失败: {e}")
        await list_cd.finish(message)
