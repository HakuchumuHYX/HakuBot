from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from ..plugin_manager.enable import is_plugin_enabled
from ..utils.image_utils import path_to_base64_image
from .api import (
    fetch_manga_detail,
    fetch_manga_list,
    fetch_random_manga,
    get_manga_image_source,
    get_manga_message_lines,
)
from .models import MangaDetail
from .render import render_manga_list_pic

manga_detail = on_command("看漫画", aliases={"看四格", "看manga"}, priority=5, block=True)
manga_list = on_command("漫画列表", aliases={"四格列表", "manga列表"}, priority=5, block=True)
manga_random = on_command("随机漫画", aliases={"随机四格", "随机manga"}, priority=5, block=True)


async def _check_enabled(event: Event) -> bool:
    if isinstance(event, GroupMessageEvent):
        user_id = str(event.get_user_id())
        return is_plugin_enabled("pjsk_mangas", str(event.group_id), user_id)
    return True


def _build_manga_message(manga: MangaDetail) -> Message:
    text = "\n".join(get_manga_message_lines(manga)) + "\n"
    image_source = get_manga_image_source(manga)
    message = Message(text)
    if image_source.startswith(("http://", "https://", "base64://")):
        message.append(MessageSegment.image(file=image_source))
    else:
        message.append(path_to_base64_image(image_source))
    return message


@manga_detail.handle()
async def handle_manga_detail(bot: Bot, event: Event, args: Message = CommandArg()):
    if not await _check_enabled(event):
        await manga_detail.finish()

    manga_id = args.extract_plain_text().strip()
    if not manga_id.isdigit():
        await manga_detail.finish("请提供正确的漫画 ID，例如：看漫画 354")

    await manga_detail.send(f"正在获取漫画 {manga_id}...")

    try:
        result = await fetch_manga_detail(manga_id)
        if isinstance(result, str):
            await manga_detail.finish(result)
        await manga_detail.finish(_build_manga_message(result))
    except FinishedException:
        raise
    except Exception as e:
        await manga_detail.finish(f"获取漫画失败: {e}")


@manga_list.handle()
async def handle_manga_list(bot: Bot, event: Event):
    if not await _check_enabled(event):
        await manga_list.finish()

    await manga_list.send("正在获取漫画列表，请稍候...")

    try:
        mangas = await fetch_manga_list(limit=20)
        pic = await render_manga_list_pic(mangas)
        await manga_list.finish(MessageSegment.image(pic))
    except FinishedException:
        raise
    except Exception as e:
        await manga_list.finish(f"获取漫画列表失败: {e}")


@manga_random.handle()
async def handle_random_manga(bot: Bot, event: Event):
    if not await _check_enabled(event):
        await manga_random.finish()

    await manga_random.send("正在随机抽取漫画...")

    try:
        result = await fetch_random_manga()
        if isinstance(result, str):
            await manga_random.finish(result)
        await manga_random.finish(_build_manga_message(result))
    except FinishedException:
        raise
    except Exception as e:
        await manga_random.finish(f"获取随机漫画失败: {e}")
