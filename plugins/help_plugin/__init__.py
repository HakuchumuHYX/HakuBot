from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GROUP, GroupMessageEvent
from nonebot.matcher import Matcher

from core.settings import load_config
from plugins.help_plugin.config import HelpConfig
from core.access import switch_rule
from utils.onebot.rules import command_boundary

help_cmd = on_command(
    "help",
    aliases={"帮助", "菜单"},
    rule=command_boundary & switch_rule("help_plugin"),
    permission=GROUP,
    priority=5,
    block=True,
)


@help_cmd.handle()
async def handle_help(event: GroupMessageEvent, matcher: Matcher):
    # 每次现读，改了 config 不用重启
    try:
        config = load_config("help_plugin", HelpConfig)
    except Exception:
        logger.exception("读取帮助配置失败，不回复帮助")
        return

    await matcher.send(config.text)
