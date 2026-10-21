"""Divorce matcher and cooldown check."""

import random

from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent
from nonebot.plugin.on import on_command
from nonebot.typing import T_State

from plugins.groupmate_waifu import service
from plugins.groupmate_waifu.constants import BYE_MESSAGES
from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown

BYE_ID = "groupmate_waifu:bye"


async def bye_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    msg = event.message.extract_plain_text().strip()
    if msg not in ["离婚", "分手"]:
        return False

    return service.has_active_partner(event.group_id, event.user_id)


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

    service.remove_couple(event.group_id, event.user_id)
    mark_cooldown(BYE_ID, event)

    await bye.finish(random.choice(BYE_MESSAGES))
