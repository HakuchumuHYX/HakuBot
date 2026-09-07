from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from core.access import is_plugin_enabled
from utils.onebot.help import send_help
from utils.rendering.models import HelpDocument, HelpSection, HelpEntry
from .help_content import HELP_DATA

HELP = HelpDocument(
    "Stickers 插件帮助",
    sections=tuple(
        HelpSection(
            section["category"],
            tuple(
                HelpEntry(item["cmd"], item["desc"], example=item.get("eg", ""))
                for item in section["commands"]
            ),
        )
        for section in HELP_DATA
    ),
)

help_matcher = on_command(
    "sticker帮助",
    aliases={"sticker help", "stickers help", "stickers帮助", "表情包帮助"},
    priority=5,
    block=True,
)


@help_matcher.handle()
async def handle_help(event: GroupMessageEvent):
    if not is_plugin_enabled("stickers", str(event.group_id), str(event.user_id)):
        return
    await send_help(help_matcher, HELP)
