import re
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher

from plugins.help_plugin.config import load_config
from core.access import is_plugin_enabled
from utils.onebot.media import image_segment
from utils.onebot.forward import send_forward_msg
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"

help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)


@help_cmd.handle()
async def handle_help(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    # 检查插件是否启用
    user_id = str(event.user_id)
    if not is_plugin_enabled("help_plugin", str(event.group_id), user_id):
        return

    config = load_config()
    try:
        image = await render_template(
            TEMPLATE_DIR,
            "help.html",
            width=750,
            title=config.bot_name,
            help_text=config.help_text,
        )
        await matcher.send(image_segment(image))
    except Exception:
        # 全局菜单是手写文本，渲染失败时原样发出去
        logger.exception("帮助图片渲染失败，改发文本")
        await matcher.send("\n\n".join([config.bot_name, *config.help_text]))

    full_text = "\n".join(config.help_text)
    links = list(dict.fromkeys(re.findall(r"https?://[^\s<>]+", full_text)))
    if links:
        forward_nodes = ["包含的链接如下："]
        for index, link in enumerate(links, 1):
            forward_nodes.append(f"{index}. {link}")

        await send_forward_msg(bot, group_id=event.group_id, items=forward_nodes)
