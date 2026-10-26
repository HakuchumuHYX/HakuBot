"""娶群友、离婚、本群CP、查看群友卡池"""

import asyncio
import random
from collections import defaultdict

from nonebot import logger
from nonebot.adapters.onebot.v11 import GROUP, ActionFailed, Bot, GroupMessageEvent
from nonebot.plugin.on import on_command, on_message
from nonebot.typing import T_State

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from plugins.groupmate_waifu import store
from plugins.groupmate_waifu.config import config
from plugins.groupmate_waifu.render import (
    display_name,
    group_names,
    member_name,
    render_list,
    render_member_pool,
    send_avatar,
)
from utils.logging import get_exc_desc
from utils.onebot.messages import mentioned_users
from utils.onebot.rules import command_boundary

BYE_ID = "groupmate_waifu:bye"

NO_WAIFU_MESSAGES = [
    "你没有娶到群友，强者注定孤独，加油！",
    "找不到对象.jpg",
    "恭喜你没有娶到老婆~",
    "さんが群友で結婚するであろうヒロインは、\n『自分の左手』です！",
]
HAPPY_END_MESSAGES = [
    "好耶~",
    "需要咱主持婚礼吗qwq",
    "不许秀恩爱！",
    "(响起婚礼进行曲♪)",
    "祝你们生八个。",
]
BYE_MESSAGES = ["嗯。", "...", "好。"]

TIPS = "你的群友結婚对象是、"


def _marriage_pool(group_id: int, members: list[dict], now: int) -> list[dict]:
    record = store.group(group_id)
    rule_out = store.protected(group_id) | record.has_result()
    last_sent_after = now - config.waifu_last_sent_time_filter
    return [
        member
        for member in members
        if member["user_id"] not in rule_out
        and member["last_sent_time"] > last_sent_after
    ]


async def _name(bot: Bot, group_id: int, user_id: int) -> str:
    member = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
    return member_name(member)


# --- 娶群友 ---


async def waifu_rule(event: GroupMessageEvent, state: T_State) -> bool:
    msg = event.message.extract_plain_text()
    at_list = mentioned_users(event.message)
    if msg != "娶群友" and not (msg.startswith("娶群友") and at_list):
        return False
    # 自己在保护名单里就是不想玩，不回应
    if event.user_id in store.protected(event.group_id):
        return False
    state["target"] = at_list[0] if at_list else None
    return True


waifu = on_message(
    rule=waifu_rule & switch_rule("groupmate_waifu"), priority=10, block=True
)

# 按群串行：几次 await 之间两个人同时娶同一个目标会都成功
_group_locks = defaultdict(asyncio.Lock)


@waifu.handle()
async def handle_waifu(bot: Bot, event: GroupMessageEvent, state: T_State):
    async with _group_locks[event.group_id]:
        await _marry(bot, event, state["target"])


async def _show_couple(
    bot: Bot, group_id: int, user_id: int, target: int | None
) -> None:
    record = store.group(group_id)
    couple = record.couple_of(user_id)
    partner = couple.partner_of(user_id)
    try:
        name = await _name(bot, group_id, partner)
    except ActionFailed as e:
        # CP 已经退群，拆掉后按没有 CP 继续娶
        logger.info(
            f"群 {group_id} 查不到 {user_id} 的 CP {partner}，按已退群拆掉: {get_exc_desc(e)}"
        )
        record.couples.remove(couple)
        store.save()
        return

    if target is None or target == user_id:
        await send_avatar(waifu, TIPS, partner, f"『{name}』！")
    if target != partner:
        await send_avatar(
            waifu, "你已经有CP了，不许花心哦~", partner, f"你的CP：{name}"
        )

    suffix = ""
    if couple.wife == user_id:
        couple.locked = True
        store.save()
        suffix = "\ncp已锁！"
    await send_avatar(
        waifu, "这是你的CP！" + random.choice(HAPPY_END_MESSAGES), partner, suffix
    )


async def _marry(bot: Bot, event: GroupMessageEvent, target: int | None) -> None:
    group_id, user_id = event.group_id, event.user_id
    record = store.group(group_id)

    if record.couple_of(user_id):
        await _show_couple(bot, group_id, user_id, target)
    if user_id in record.singles:
        await waifu.finish("你今天已经单身了，明天再来吧~", at_sender=True)

    tips = TIPS
    if target:
        if target in store.protected(group_id):
            await waifu.finish("对方受到保护，不可以娶哦！", at_sender=True)
        if target == user_id:
            await waifu.finish("不可以娶自己哦！", at_sender=True)
        # 今天单身的人被指定必定娶到
        roll = 1 if target in record.singles else random.randint(1, 100)
        if roll <= config.waifu_he:
            waifu_id = target
            tips = "恭喜你娶到了群友!\n" + TIPS
        elif roll <= config.waifu_he + config.waifu_be:
            waifu_id = user_id
        else:
            try:
                name = await _name(bot, group_id, target)
            except ActionFailed as e:
                logger.warning(
                    f"群 {group_id} 查询成员 {target} 失败: {get_exc_desc(e)}"
                )
                name = "TA"
            await waifu.finish(f"你没能娶到 {name}！", at_sender=True)
    else:
        members = await bot.get_group_member_list(group_id=group_id)
        pool = _marriage_pool(group_id, members, event.time)
        if not pool:
            await waifu.finish(
                "群友已经被娶光了、\n" + random.choice(NO_WAIFU_MESSAGES),
                at_sender=True,
            )
        # 卡池里有自己，抽到自己就是单身
        waifu_id = random.choice(pool)["user_id"]

    if waifu_id == user_id:
        record.singles.add(user_id)
        store.save()
        await waifu.finish(random.choice(NO_WAIFU_MESSAGES), at_sender=True)

    # 卡池排除了有 CP 的人，只有指定娶会碰上
    taken = record.couple_of(waifu_id)
    if taken:
        rival = taken.partner_of(waifu_id)
        prefix = "人家已经名花有主了~"
        suffix = "ta的cp：" + await _name(bot, group_id, rival)
        if taken.locked or random.randint(1, 100) > config.waifu_ntr:
            record.singles.add(user_id)
            store.save()
            if taken.locked:
                suffix += "\n本对cp已锁！"
            await send_avatar(waifu, prefix, rival, suffix)
        await send_avatar(waifu, prefix, rival, suffix + "\n但是...", finish=False)
        await asyncio.sleep(1)

    record.marry(user_id, waifu_id)
    store.save()
    name = await _name(bot, group_id, waifu_id)
    await send_avatar(waifu, tips, waifu_id, f"『{name}』！")


# --- 离婚 ---


async def bye_rule(event: GroupMessageEvent) -> bool:
    if event.message.extract_plain_text().strip() not in ("离婚", "分手"):
        return False
    return store.group(event.group_id).couple_of(event.user_id) is not None


bye = on_command(
    "离婚",
    aliases={"分手"},
    rule=bye_rule & switch_rule(BYE_ID),
    permission=GROUP,
    priority=5,
    block=True,
)


@bye.handle()
async def handle_bye(event: GroupMessageEvent):
    remaining_cd = cooldown_remaining(BYE_ID, event)
    if remaining_cd > 0:
        await bye.finish(f"你的离婚cd还有 {remaining_cd} 秒。", at_sender=True)

    record = store.group(event.group_id)
    record.couples.remove(record.couple_of(event.user_id))
    store.save()
    mark_cooldown(BYE_ID, event)
    await bye.finish(random.choice(BYE_MESSAGES))


# --- 列表 ---

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
    members = await bot.get_group_member_list(group_id=event.group_id)
    pool = _marriage_pool(event.group_id, members, event.time)
    if not pool:
        await waifu_list.finish("群友已经被娶光了。")
    pool.sort(key=lambda member: member["last_sent_time"], reverse=True)
    await waifu_list.finish(await render_member_pool("卡池", pool))


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
    couples = store.group(event.group_id).couples
    if not couples:
        await cp_list.finish("本群暂无cp哦~")

    names = await group_names(bot, event.group_id)
    pairs = [
        (c.husband, display_name(names, c.husband), c.wife, display_name(names, c.wife))
        for c in couples
    ]
    await cp_list.finish(await render_list("本群CP", pairs=pairs))
