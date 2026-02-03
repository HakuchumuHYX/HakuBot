"""
管理命令：hltv开启 / hltv关闭 / hltv启用 / hltv禁用
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.exception import FinishedException

from ..data_manager import data_manager
from ..permissions import check_permission
from ..scheduler import hltv_scheduler


hltv_toggle = on_command(
    "hltv开启",
    aliases={"hltv关闭", "hltv启用", "hltv禁用"},
    priority=5,
    block=True,
)


@hltv_toggle.handle()
async def handle_hltv_toggle(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    user_id = event.user_id

    if not await check_permission(bot, group_id, user_id):
        await hltv_toggle.finish("❌ 需要管理员权限")
        return

    raw_cmd = event.get_plaintext().strip()

    try:
        if "开启" in raw_cmd or "启用" in raw_cmd:
            data_manager.set_enabled(group_id, True)
            hltv_scheduler.ensure_job_state()
            await hltv_toggle.finish("✅ HLTV 订阅功能已开启")
        else:
            data_manager.set_enabled(group_id, False)
            hltv_scheduler.ensure_job_state()
            await hltv_toggle.finish("❌ HLTV 订阅功能已关闭")
    except FinishedException:
        raise
