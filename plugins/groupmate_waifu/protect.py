"""保护名单：在名单里的人不会被娶、被透，自己也不能娶、透别人"""

from nonebot.adapters.onebot.v11 import (
    GROUP,
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
)
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command

from core.access import switch_rule
from plugins.groupmate_waifu import store
from plugins.groupmate_waifu.render import display_name, group_names, render_list
from utils.onebot.messages import mentioned_users
from utils.onebot.rules import command_boundary

ADMIN = SUPERUSER | GROUP_ADMIN | GROUP_OWNER


async def _name_lines(bot: Bot, group_id: int, user_ids) -> str:
    names = await group_names(bot, group_id)
    return "\n".join(display_name(names, user_id) for user_id in user_ids)


protect = on_command(
    "娶群友保护",
    rule=command_boundary & switch_rule("groupmate_waifu"),
    permission=GROUP,
    priority=5,
    block=True,
)


@protect.handle()
async def handle_protect(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    at = mentioned_users(event.message)

    if not at:
        store.protected(group_id).add(event.user_id)
        store.save_protected()
        await protect.finish("保护成功！", at_sender=True)
    if not await ADMIN(bot, event):
        await protect.finish("保护失败。你无法为其他人设置保护。", at_sender=True)

    store.protected(group_id).update(at)
    store.save_protected()
    names = await _name_lines(bot, group_id, at)
    await protect.finish(f"保护成功！\n保护名单为：\n{names}", at_sender=True)


unprotect = on_command(
    "解除娶群友保护",
    rule=command_boundary & switch_rule("groupmate_waifu"),
    permission=GROUP,
    priority=5,
    block=True,
)


@unprotect.handle()
async def handle_unprotect(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    protected = store.protected(group_id)
    at = mentioned_users(event.message)

    if not at:
        if event.user_id not in protected:
            await unprotect.finish("你不在保护名单内。", at_sender=True)
        protected.discard(event.user_id)
        store.save_protected()
        await unprotect.finish("解除保护成功！", at_sender=True)
    if not await ADMIN(bot, event):
        await unprotect.finish("解除保护失败。你无法为其他人解除保护。", at_sender=True)

    removed = protected & set(at)
    if not removed:
        await unprotect.finish("保护名单内不存在指定成员。", at_sender=True)
    protected -= removed
    store.save_protected()
    names = await _name_lines(bot, group_id, removed)
    await unprotect.finish(f"解除保护成功！\n解除保护名单为：\n{names}", at_sender=True)


show_protect = on_command(
    "查看保护名单",
    rule=command_boundary & switch_rule("groupmate_waifu"),
    permission=GROUP,
    priority=5,
    block=True,
)


@show_protect.handle()
async def handle_show_protect(bot: Bot, event: GroupMessageEvent):
    protected = store.protected(event.group_id)
    if not protected:
        await show_protect.finish("保护名单为空")
    names = await group_names(bot, event.group_id)
    await show_protect.finish(
        await render_list(
            "保护名单", names=[display_name(names, user_id) for user_id in protected]
        )
    )
