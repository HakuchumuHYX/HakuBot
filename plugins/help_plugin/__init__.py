from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.matcher import Matcher

from core.settings import load_config
from plugins.help_plugin.config import HelpConfig
from core.access import is_plugin_enabled

help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)


@help_cmd.handle()
async def handle_help(event: GroupMessageEvent, matcher: Matcher):
    if not is_plugin_enabled("help_plugin", str(event.group_id), str(event.user_id)):
        return

    # 每次现读，改了 config 不用重启
    try:
        config = load_config("help_plugin", HelpConfig)
    except Exception:
        logger.exception("读取帮助配置失败，不回复帮助")
        return

    await matcher.send(config.text)
