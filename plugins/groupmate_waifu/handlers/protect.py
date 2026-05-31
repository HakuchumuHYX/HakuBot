"""Protection-list matchers for marry/yinpa opt-out behavior."""

from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, Bot, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command

from .. import service
from ..render import render_protect_list
from ..rules import check_plugin_enabled
from ..utils import get_message_at
from ...utils.common import create_exact_command_rule


protect = on_command(
    "娶群友保护",
    priority=10,
    block=True,
    rule=create_exact_command_rule("娶群友保护", extra_rule=check_plugin_enabled),
)


@protect.handle()
async def handle_protect(bot: Bot, event: GroupMessageEvent):
    permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
    group_id = event.group_id
    at = get_message_at(event.message)

    if not at:
        service.protect_users(group_id, [event.user_id])
        await protect.finish("保护成功！", at_sender=True)
    elif await permission(bot, event):
        service.protect_users(group_id, at)
        namelist = "\n".join([
            (member["card"] or member["nickname"])
            for user_id in at
            if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
        ])
        await protect.finish(f"保护成功！\n保护名单为：\n{namelist}", at_sender=True)
    else:
        await protect.finish("保护失败。你无法为其他人设置保护。", at_sender=True)


unprotect = on_command(
    "解除娶群友保护",
    priority=10,
    block=True,
    rule=create_exact_command_rule("解除娶群友保护", extra_rule=check_plugin_enabled),
)


@unprotect.handle()
async def handle_unprotect(bot: Bot, event: GroupMessageEvent):
    permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
    group_id = event.group_id
    at = get_message_at(event.message)

    if not at:
        removed_users = service.unprotect_users(group_id, [event.user_id])
        if removed_users:
            await unprotect.finish("解除保护成功！", at_sender=True)
        else:
            await unprotect.finish("你不在保护名单内。", at_sender=True)
    elif await permission(bot, event):
        valid_at = service.unprotect_users(group_id, at)
        if not valid_at:
            await unprotect.finish("保护名单内不存在指定成员。", at_sender=True)
        namelist = "\n".join([
            (member["card"] or member["nickname"])
            for user_id in valid_at
            if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
        ])
        await unprotect.finish(f"解除保护成功！\n解除保护名单为：\n{namelist}", at_sender=True)
    else:
        await unprotect.finish("解除保护失败。你无法为其他人解除保护。", at_sender=True)


show_protect = on_command(
    "查看保护名单",
    priority=10,
    block=True,
    rule=create_exact_command_rule("查看保护名单", extra_rule=check_plugin_enabled),
)


@show_protect.handle()
async def handle_show_protect(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    protect_set = service.get_protected_users(group_id)

    if not protect_set:
        await show_protect.finish("保护名单为空")

    names = [
        (member["card"] or member["nickname"])
        for user_id in protect_set
        if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
    ]
    await show_protect.finish(render_protect_list(names))
