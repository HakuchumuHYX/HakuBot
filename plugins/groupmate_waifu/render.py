"""头像消息、群名片、列表图"""

from pathlib import Path
from collections.abc import Mapping, Sequence

from nonebot import logger
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, MessageSegment
from nonebot.matcher import Matcher

from utils.logging import get_exc_desc
from utils.onebot.avatar import download_avatar
from utils.onebot.media import image_segment
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"


def member_name(member: Mapping) -> str:
    return member["card"] or member["nickname"]


async def group_names(bot: Bot, group_id: int) -> dict[int, str]:
    members = await bot.get_group_member_list(group_id=group_id)
    return {member["user_id"]: member_name(member) for member in members}


async def send_avatar(
    matcher: type[Matcher],
    prefix: str,
    user_id: int,
    suffix: str = "",
    *,
    finish: bool = True,
) -> None:
    """发“文字 + 头像 + 文字”并 at 发送者

    头像下载失败、带图发送失败都退成纯文字：头像走外网，NapCat 发图也失败过。
    """
    text = prefix + suffix
    try:
        message = prefix + image_segment(await download_avatar(user_id, size=640))
        message += suffix
    except Exception as e:
        logger.warning(f"获取用户头像失败，发送文本结果: user_id={user_id} error={e}")
        message = text
    try:
        await matcher.send(message, at_sender=True)
    except ActionFailed as e:
        logger.warning(f"发送图片消息失败，降级为文本: {get_exc_desc(e)}")
        await matcher.send(text, at_sender=True)
    if finish:
        await matcher.finish()


async def render_list(title: str, **context) -> MessageSegment:
    image = await render_template(
        TEMPLATE_DIR, "list.html", width=640, title=title, **context
    )
    return image_segment(image)


async def render_member_pool(
    title: str, members: Sequence[Mapping], limit: int = 80
) -> MessageSegment:
    subtitle = f"共 {len(members)} 人"
    if len(members) > limit:
        subtitle += f"，仅显示前 {limit} 人"
    names = [member_name(member) for member in members[:limit]]
    return await render_list(title, subtitle=subtitle, names=names)


def display_name(names: dict[int, str], user_id: int) -> str:
    """退群的人查不到名片，显示 QQ 号"""
    return names.get(user_id, str(user_id))
