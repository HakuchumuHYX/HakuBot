"""Read-only list/query matchers for pools and current couples."""

from nonebot.adapters.onebot.v11 import GROUP, ActionFailed, Bot, GroupMessageEvent
from nonebot.plugin.on import on_command

from plugins.groupmate_waifu import service
from plugins.groupmate_waifu.render import render_cp_list, render_member_pool
from core.access import switch_rule
from utils.onebot.rules import command_boundary


waifu_list = on_command(
    "查看群友卡池",
    aliases={"群友卡池"},
    rule=command_boundary & switch_rule("groupmate_waifu"),
    permission=GROUP,
    priority=5,
    block=True,
)


@waifu_list.handle()
async def handle_waifu_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    member_list = await bot.get_group_member_list(group_id=group_id)
    lastmonth = event.time - service.get_last_sent_time_filter()

    member_list = service.get_marriage_pool_members(group_id, member_list, lastmonth)
    member_list.sort(key=lambda x: x["last_sent_time"], reverse=True)

    if member_list:
        await waifu_list.finish(await render_member_pool("卡池", member_list))
    else:
        await waifu_list.finish("群友已经被娶光了。")


cp_list = on_command(
    "本群CP",
    aliases={"本群cp"},
    rule=command_boundary & switch_rule("groupmate_waifu"),
    permission=GROUP,
    priority=5,
    block=True,
)


@cp_list.handle()
async def handle_cp_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    cp_pairs = service.get_cp_pairs(group_id)

    if not cp_pairs:
        await cp_list.finish("本群暂无cp哦~")

    names = []
    for user_id, waifu_id in cp_pairs:
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
            name_a = member["card"] or member["nickname"]
        except ActionFailed:
            name_a = str(user_id)

        try:
            member = await bot.get_group_member_info(
                group_id=group_id, user_id=waifu_id
            )
            name_b = member["card"] or member["nickname"]
        except ActionFailed:
            name_b = str(waifu_id)

        names.append((user_id, name_a, waifu_id, name_b))

    await cp_list.finish(await render_cp_list(names))
