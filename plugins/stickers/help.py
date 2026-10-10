from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from core.access import is_plugin_enabled
from utils.onebot.media import image_segment
from utils.rendering.engine import render_template
from .help_content import HELP_DATA
from .send import get_folder_display_info, get_random_stickers

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
    folders = get_folder_display_info()
    image = await render_template(
        TEMPLATE_DIR,
        "help.html",
        width=780,
        help_data=HELP_DATA,
        covers=[p.resolve().as_uri() for p in get_random_stickers("stickers", 7)],
        folder_count=len(folders),
        image_count=sum(f["image_count"] for f in folders),
    )
    await help_matcher.finish(image_segment(image))
