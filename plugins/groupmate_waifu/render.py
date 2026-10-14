"""
Message rendering helpers for groupmate_waifu.

This module owns image generation, avatar message composition, and fallback
send/finish behavior. It should not mutate plugin state.
"""

from pathlib import Path
from typing import Iterable, Mapping, Sequence, Tuple

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot import logger

from utils.logging import get_exc_desc
from utils.onebot.media import image_segment
from utils.onebot.forward import ForwardItem
from utils.rendering.engine import render_template
from plugins.groupmate_waifu.utils import download_user_img

TEMPLATE_DIR = Path(__file__).parent / "templates"


async def user_img(user_id: int) -> bytes:
    return await download_user_img(user_id)


async def user_img_segment(user_id: int) -> MessageSegment:
    try:
        return image_segment(await user_img(user_id))
    except Exception as e:
        logger.warning(f"获取用户头像失败，发送文本结果: user_id={user_id} error={e}")
        return MessageSegment.text("")


async def build_avatar_message(prefix: str, user_id: int, suffix: str = "") -> tuple:
    fallback = prefix + suffix
    message = prefix + (await user_img_segment(user_id)) + suffix
    return message, fallback


def _log_send_fallback(e: ActionFailed):
    logger.warning(f"发送图片消息失败，降级为文本: {get_exc_desc(e)}")


async def send_with_fallback(matcher, message, fallback: str, **kwargs):
    try:
        await matcher.send(message, **kwargs)
    except ActionFailed as e:
        _log_send_fallback(e)
        await matcher.send(fallback, **kwargs)


async def finish_with_fallback(matcher, message, fallback: str, **kwargs):
    try:
        await matcher.finish(message, **kwargs)
    except ActionFailed as e:
        _log_send_fallback(e)
        await matcher.finish(fallback, **kwargs)


async def _render_list(title: str, **context) -> MessageSegment:
    image = await render_template(
        TEMPLATE_DIR, "list.html", width=640, title=title, **context
    )
    return image_segment(image)


async def render_protect_list(names: Iterable[str]) -> MessageSegment:
    return await _render_list("保护名单", names=list(names))


async def render_member_pool(
    title: str, members: Sequence[Mapping], limit: int = 80
) -> MessageSegment:
    names = [(member["card"] or member["nickname"]) for member in members[:limit]]
    subtitle = f"共 {len(members)} 人"
    if len(members) > limit:
        subtitle += f"，仅显示前 {limit} 人"
    return await _render_list(title, subtitle=subtitle, names=names)


async def render_cp_list(
    pairs: Iterable[Tuple[int, str, int, str]],
) -> MessageSegment:
    return await _render_list("本群CP", pairs=list(pairs))


def make_forward_node(name: str, uin: int, content) -> ForwardItem:
    return ForwardItem(content=content, name=name, uin=uin)


async def render_yinpa_record(
    title: str, rows: Iterable[Tuple[int, str, int]], action: str
) -> MessageSegment:
    return await _render_list(title, records=list(rows), action=action)
