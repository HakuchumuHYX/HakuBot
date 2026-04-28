from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from ..utils.image_utils import path_to_base64_image
from .client import PixivClient, PixivClientError, PixivSendForwardError
from .config import config
from .formatter import (
    PixivPolicy,
    build_forward_contents,
    build_info_text,
    detect_image_ext,
    describe_client_error,
    parse_pid,
    policy_allows,
    select_pages,
    should_use_forward,
)
from .models import PixivIllust, PixivPage

try:
    from ..plugin_manager.cd_manager import check_cd, update_cd
    from ..plugin_manager.enable import is_plugin_enabled

    MANAGER_AVAILABLE = True
except Exception:
    MANAGER_AVAILABLE = False


PLUGIN_ID = "pixiv_id_fetcher"

pixiv_cmd = on_command("pixiv", aliases={"pid", "p站图"}, priority=5, block=True)

_client: Optional[PixivClient] = None
_semaphore: Optional[asyncio.Semaphore] = None


def _get_client() -> PixivClient:
    global _client
    if _client is None:
        _client = PixivClient(
            refresh_token=config.refresh_token,
            client_id=str(config.get("client_id")),
            client_secret=str(config.get("client_secret")),
            proxy=config.proxy,
            timeout=float(config.get("timeout", 20.0)),
            reverse_proxy_domain=config.get("reverse_proxy_domain"),
        )
    return _client


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        concurrency = int(config.get("concurrency", 2) or 2)
        _semaphore = asyncio.Semaphore(max(1, concurrency))
    return _semaphore


def _cache_path(illust: PixivIllust, page: PixivPage) -> Path:
    return localstore.get_plugin_cache_file(f"pixiv_{illust.pid}_p{page.index}.{page.ext}")


def _cache_path_with_ext(illust: PixivIllust, page: PixivPage, ext: str) -> Path:
    return localstore.get_plugin_cache_file(f"pixiv_{illust.pid}_p{page.index}.{ext}")


def _ugoira_cache_path(illust: PixivIllust) -> Path:
    return localstore.get_plugin_cache_file(f"pixiv_{illust.pid}_ugoira.gif")


async def _download_page(
    illust: PixivIllust,
    page: PixivPage,
    *,
    normalize_for_forward: bool = False,
) -> MessageSegment:
    if normalize_for_forward:
        forward_path = _cache_path_with_ext(illust, page, "jpg")
        if forward_path.exists() and forward_path.stat().st_size > 0:
            return path_to_base64_image(forward_path)

    path = _cache_path(illust, page)
    if path.exists() and path.stat().st_size > 0:
        if not normalize_for_forward:
            return path_to_base64_image(path)
        data = path.read_bytes()
        ext = detect_image_ext(data, path.suffix.lstrip(".") or page.ext)
        data, ext = _get_client().normalize_static_image_for_forward(data, ext)
        path = _cache_path_with_ext(illust, page, ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path_to_base64_image(path)

    data = await _get_client().download_image(
        page.url,
        max_bytes=int(config.get("max_bytes", 20 * 1024 * 1024)),
    )
    ext = detect_image_ext(data, page.ext)
    if normalize_for_forward:
        data, ext = _get_client().normalize_static_image_for_forward(data, ext)
    path = _cache_path_with_ext(illust, page, ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path_to_base64_image(path)


async def _download_ugoira(illust: PixivIllust) -> MessageSegment:
    path = _ugoira_cache_path(illust)
    if path.exists() and path.stat().st_size > 0:
        return path_to_base64_image(path)

    client = _get_client()
    metadata = await client.fetch_ugoira_metadata(illust.pid)
    zip_data = await client.download_bytes(
        metadata.zip_url,
        max_bytes=int(config.get("ugoira_zip_max_bytes", 30 * 1024 * 1024)),
    )
    gif_data = client.render_ugoira_gif(
        zip_data,
        metadata,
        max_frames=int(config.get("ugoira_max_frames", 150)),
        max_bytes=int(config.get("max_bytes", 20 * 1024 * 1024)),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gif_data)
    return path_to_base64_image(path)


def _build_image_message(illust: PixivIllust, image: MessageSegment) -> Message:
    return MessageSegment.text(build_info_text(illust) + "\n") + image


async def _send_direct(
    bot: Bot,
    event: MessageEvent,
    illust: PixivIllust,
    page: Optional[PixivPage],
) -> None:
    if illust.is_ugoira:
        image = await _download_ugoira(illust)
    else:
        if page is None:
            raise PixivClientError("没有找到可发送的图片")
        image = await _download_page(illust, page)
    await bot.send(event, _build_image_message(illust, image))


async def _send_forward(
    bot: Bot,
    event: MessageEvent,
    illust: PixivIllust,
    pages: List[PixivPage],
    *,
    truncated: bool,
) -> None:
    images: List[MessageSegment] = []
    if illust.is_ugoira:
        image = await _download_ugoira(illust)
        images.append(image)
    else:
        for page in pages:
            image = await _download_page(illust, page, normalize_for_forward=True)
            images.append(image)

    messages = build_forward_contents(illust, images)
    if truncated:
        messages.append(Message(f"多页作品，仅发送前 {len(pages)} / {illust.page_count} 页"))

    await _send_forward_without_image_fallback(bot, event, messages)


async def _send_forward_without_image_fallback(
    bot: Bot,
    event: MessageEvent,
    messages: List[object],
) -> None:
    try:
        login_info = await bot.get_login_info()
        user_id = str(login_info.get("user_id", event.self_id))
        nickname = str(login_info.get("nickname", "Bot"))
    except Exception:
        user_id = str(event.self_id)
        nickname = "Bot"

    nodes = [
        {
            "type": "node",
            "data": {
                "name": nickname,
                "uin": user_id,
                "content": str(msg),
            },
        }
        for msg in messages
    ]

    try:
        if isinstance(event, GroupMessageEvent):
            await bot.send_forward_msg(group_id=int(event.group_id), messages=nodes)
        else:
            await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)
    except ActionFailed as e:
        raise PixivSendForwardError(str(e)) from e
    except Exception as e:
        raise PixivSendForwardError(str(e)) from e


@pixiv_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.get_user_id())
    group_id: Optional[str] = None

    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        if MANAGER_AVAILABLE:
            if not is_plugin_enabled(PLUGIN_ID, group_id, user_id):
                await pixiv_cmd.finish()

            remaining = check_cd(PLUGIN_ID, group_id, user_id)
            if remaining > 0:
                await pixiv_cmd.finish(f"功能冷却中，请等待 {remaining} 秒", at_sender=True)

    pid = parse_pid(args.extract_plain_text())
    if pid is None:
        await pixiv_cmd.finish("格式：pixiv <pid>，也支持 Pixiv 作品链接")

    await pixiv_cmd.send("在找了在找了...")

    policy = PixivPolicy(
        allow_r18=bool(config.get("allow_r18", False)),
        allow_r18g=bool(config.get("allow_r18g", False)),
    )

    try:
        async with _get_semaphore():
            illust = await _get_client().fetch_illust(
                pid,
                send_original=bool(config.get("send_original", False)),
            )

            if not policy_allows(illust, policy):
                if illust.is_r18g:
                    await pixiv_cmd.finish("该作品为 R18G，当前配置未允许发送")
                await pixiv_cmd.finish("该作品为 R18，当前配置未允许发送")

            if not illust.is_ugoira and not illust.pages:
                await pixiv_cmd.finish("没有找到可发送的图片")

            pages, truncated = select_pages(illust, int(config.get("max_pages", 9) or 9))

            if should_use_forward(illust):
                await _send_forward(bot, event, illust, pages, truncated=truncated)
            else:
                await _send_direct(bot, event, illust, pages[0] if pages else None)

            if group_id and MANAGER_AVAILABLE:
                update_cd(PLUGIN_ID, group_id, user_id)

    except FinishedException:
        raise
    except PixivClientError as e:
        logger.warning(f"[pixiv_id_fetcher] request failed pid={pid} kind={e.kind}: {e}")
        await pixiv_cmd.finish(describe_client_error(e.kind))
    except Exception as e:
        logger.exception(f"[pixiv_id_fetcher] unexpected error pid={pid}: {e}")
        await pixiv_cmd.finish("获取 Pixiv 图片失败，请稍后再试")

    await pixiv_cmd.finish()
