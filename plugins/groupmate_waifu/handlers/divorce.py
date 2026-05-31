"""Divorce matcher and cooldown check."""

import random

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.plugin.on import on_command
from nonebot.typing import T_State

from .. import service
from ..constants import BYE_MESSAGES, PLUGIN_NAME
from ..rules import is_bye_enabled, is_plugin_enabled
from ...plugin_manager.cd_manager import check_cd, update_cd


async def bye_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    if not is_plugin_enabled(str(event.group_id), str(event.user_id)):
        return False

    if not is_bye_enabled(str(event.group_id), str(event.user_id)):
        return False

    msg = event.message.extract_plain_text().strip()
    if msg not in ["离婚", "分手"]:
        return False

    return service.has_active_partner(event.group_id, event.user_id)


bye = on_command(
    "离婚",
    aliases={"分手"},
    rule=bye_rule,
    priority=10,
    block=True,
)


@bye.handle()
async def handle_bye(event: GroupMessageEvent):
    group_id = event.group_id
    user_id = event.user_id
    group_id_str = str(group_id)
    user_id_str = str(user_id)

    cd_feature_id = f"{PLUGIN_NAME}:bye"
    remaining_cd = check_cd(cd_feature_id, group_id_str, user_id_str)

    if remaining_cd > 0:
        await bye.finish(f"你的离婚cd还有 {remaining_cd} 秒。", at_sender=True)

    service.remove_couple(group_id, user_id)
    update_cd(cd_feature_id, group_id_str, user_id_str)

    await bye.finish(random.choice(BYE_MESSAGES))
