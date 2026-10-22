"""透群友、涩涩记录"""

import random

from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent
from nonebot.plugin.on import on_command, on_message
from nonebot.typing import T_State

from core.access import switch_rule
from plugins.groupmate_waifu import store
from plugins.groupmate_waifu.config import config
from plugins.groupmate_waifu.render import (
    member_name,
    render_list,
    render_member_pool,
    send_avatar,
)
from utils.onebot.forward import ForwardItem, send_forward_msg
from utils.onebot.messages import mentioned_users
from utils.onebot.rules import command_boundary


def _yinpa_pool(group_id: int, members: list[dict], now: int) -> list[dict]:
    protected = store.protected(group_id)
    last_sent_after = now - config.waifu_last_sent_time_filter
    return [
        member
        for member in members
        if member["user_id"] not in protected
        and member["last_sent_time"] > last_sent_after
    ]


# --- 透群友 ---


async def yinpa_rule(event: GroupMessageEvent, state: T_State) -> bool:
    msg = event.message.extract_plain_text()
    at_list = mentioned_users(event.message)
    if msg != "透群友" and not (msg.startswith("透群友") and at_list):
        return False
    if event.user_id in store.protected(event.group_id):
        return False
    state["target"] = at_list[0] if at_list else None
    return True


yinpa = on_message(
    rule=yinpa_rule & switch_rule("groupmate_waifu:yinpa"), priority=10, block=True
)


@yinpa.handle()
async def handle_yinpa(bot: Bot, event: GroupMessageEvent, state: T_State):
    group_id, user_id = event.group_id, event.user_id
    record = store.group(group_id)
    target = state["target"]

    if target:
        if target == user_id:
            await send_avatar(yinpa, "恭喜你涩到了你自己！", user_id)
        if target in store.protected(group_id):
            await yinpa.finish("对方受到保护，不可以涩涩！", at_sender=True)
        couple = record.couple_of(user_id)
        if couple and couple.partner_of(user_id) == target:
            if random.randint(1, 100) > config.yinpa_cp:
                await yinpa.finish("你的老婆拒绝和你涩涩！", at_sender=True)
            tips = "恭喜你涩到了你的老婆！"
        else:
            if random.randint(1, 100) > config.yinpa_he:
                await yinpa.finish("涩涩警察出现！不许涩涩！", at_sender=True)
            tips = "恭喜你涩到了群友！"
    else:
        members = await bot.get_group_member_list(group_id=group_id)
        pool = [
            member
            for member in _yinpa_pool(group_id, members, event.time)
            if member["user_id"] != user_id
        ]
        if not pool:
            await yinpa.finish("卡池里没有可涩的人了！", at_sender=True)
        target = random.choice(pool)["user_id"]
        tips = "你的涩涩对象是、"

    record.yinpa_by[user_id] += 1
    record.yinpa_on[target] += 1
    store.save()
    member = await bot.get_group_member_info(group_id=group_id, user_id=target)
    await send_avatar(yinpa, tips, target, f"『{member_name(member)}』！")


# --- 涩涩记录 ---

yinpa_list = on_command(
    "涩涩记录",
    aliases={"色色记录"},
    rule=command_boundary & switch_rule("groupmate_waifu:yinpa"),
    permission=GROUP,
    priority=5,
    block=True,
)


@yinpa_list.handle()
async def handle_yinpa_list(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    record = store.group(group_id)
    members = await bot.get_group_member_list(group_id=group_id)
    names = {member["user_id"]: member_name(member) for member in members}

    # 只列还在群里的人
    rows_by = [
        (uid, names[uid], n) for uid, n in record.yinpa_by.most_common() if uid in names
    ]
    rows_on = [
        (uid, names[uid], n) for uid, n in record.yinpa_on.most_common() if uid in names
    ]
    if not rows_by and not rows_on:
        await yinpa_list.finish("今天还没有人涩涩哦。")

    pool = _yinpa_pool(group_id, members, event.time)
    pool.sort(key=lambda member: member["last_sent_time"], reverse=True)
    items = [ForwardItem(await render_member_pool("卡池", pool), "卡池", event.self_id)]
    if rows_by:
        image = await render_list("涩涩记录①", records=rows_by, action="今日透群友")
        items.append(ForwardItem(image, "记录①", event.self_id))
    if rows_on:
        image = await render_list("涩涩记录②", records=rows_on, action="今日被透")
        items.append(ForwardItem(image, "记录②", event.self_id))
    await send_forward_msg(bot, group_id=group_id, items=items)
