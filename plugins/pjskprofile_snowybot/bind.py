import re
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.params import RegexGroup
from nonebot.exception import FinishedException
from nonebot.log import logger
from typing import Tuple, Optional

from ..plugin_manager.enable import is_plugin_enabled
from .data_manager import update_binding
bind_matcher = on_regex(r"^(cn|jp|en|tw|kr)?\s*绑定\s*(\d+)$", priority=10, block=True)


@bind_matcher.handle()
async def _(event: MessageEvent, groups: Tuple[Optional[str], str] = RegexGroup()):
    # 插件开关检查
    if isinstance(event, GroupMessageEvent):
        user_id = str(event.get_user_id())
        if not is_plugin_enabled("pjskprofile_snowybot", str(event.group_id), user_id):
            await bind_matcher.finish()

    user_id = event.get_user_id()

    server_prefix = groups[0]
    pjsk_id = groups[1]

    if server_prefix:
        server = server_prefix.lower()
    else:
        server = "jp"

    try:
        success = update_binding(user_id, server, pjsk_id)

        if success:
            server_name_map = {
                "jp": "日服", "cn": "国服", "en": "国际服",
                "tw": "台服", "kr": "韩服"
            }
            display_server = server_name_map.get(server, server)

            await bind_matcher.finish(f"✅ 绑定成功！\n服务器: {display_server}\nID: {pjsk_id}")
        else:
            await bind_matcher.finish("❌ 绑定失败，发生未知错误。")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"PJSK 绑定指令发生错误: {e}")
        await bind_matcher.finish("❌ 处理过程中发生内部错误，请查看日志。")
