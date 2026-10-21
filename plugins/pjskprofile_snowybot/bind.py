from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import RegexGroup
from typing import Tuple, Optional

from core.access import switch_rule
from plugins.pjskprofile_snowybot.data_manager import update_binding

bind_matcher = on_regex(
    r"^(cn|jp|en|tw|kr)?\s*绑定\s*(\d+)$",
    rule=switch_rule("pjskprofile_snowybot"),
    priority=10,
    block=True,
)


@bind_matcher.handle()
async def _(event: MessageEvent, groups: Tuple[Optional[str], str] = RegexGroup()):
    user_id = event.get_user_id()

    server_prefix = groups[0]
    pjsk_id = groups[1]

    if server_prefix:
        server = server_prefix.lower()
    else:
        server = "jp"

    success = update_binding(user_id, server, pjsk_id)

    if success:
        server_name_map = {
            "jp": "日服",
            "cn": "国服",
            "en": "国际服",
            "tw": "台服",
            "kr": "韩服",
        }
        display_server = server_name_map.get(server, server)

        await bind_matcher.finish(
            f"✅ 绑定成功！\n服务器: {display_server}\nID: {pjsk_id}"
        )
    else:
        await bind_matcher.finish("❌ 绑定失败，发生未知错误。")
