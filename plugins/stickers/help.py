from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from core.access import is_plugin_enabled
from utils.onebot.media import image_segment
from utils.rendering.engine import render_template
from .help_content import HELP_DATA

TEMPLATE_DIR = Path(__file__).parent / "templates"

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
    image = await render_template(
        TEMPLATE_DIR, "help.html", width=780, help_data=HELP_DATA
    )
    await help_matcher.finish(image_segment(image))
