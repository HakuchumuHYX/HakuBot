from pathlib import Path

from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.matcher import Matcher

from plugins.help_plugin.config import HelpConfig, load_config
from core.access import is_plugin_enabled
from utils.onebot.media import image_segment
from utils.onebot.forward import send_forward_msg
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"

help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)


def _help_as_text(config: HelpConfig) -> str:
    parts = [f"{config.bot_name}\n{config.intro}"]
    for section in config.sections:
        lines = [f"【{section.title}】"]
        for item in section.items:
            trigger = f"（{' / '.join(item.triggers)}）" if item.triggers else ""
            lines.append(f"{item.name}{trigger}：{item.desc}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


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
            width=860,
            config=config,
            avatar_url=f"https://q1.qlogo.cn/g?b=qq&nk={bot.self_id}&s=640",
        )
        await matcher.send(image_segment(image))
    except Exception:
        # 全局菜单是手写文本，渲染失败时改发文字版
        logger.opt(exception=True).warning("帮助图片渲染失败，改发文本")
        await matcher.send(_help_as_text(config))

    links = [
        *config.links,
        *(link for s in config.sections for i in s.items for link in i.links),
    ]
    if links:
        forward_nodes = ["包含的链接如下："]
        for index, link in enumerate(dict.fromkeys(links), 1):
            forward_nodes.append(f"{index}. {link}")

        await send_forward_msg(bot, group_id=event.group_id, items=forward_nodes)
