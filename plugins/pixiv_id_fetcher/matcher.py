"""pixiv <pid>：单页直接发，多页和 R18 用合并转发，动图转成 GIF；图片被拒时改发链接"""

import asyncio
import re
from typing import NoReturn

import aiohttp
from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError
from nonebot.params import CommandArg

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from plugins.pixiv_id_fetcher.client import (
    Illust,
    PixivError,
    download_image,
    fetch_illust,
    fetch_ugoira_gif,
)
from plugins.pixiv_id_fetcher.config import config
from utils.network.http import DownloadTooLarge, HttpError
from utils.onebot.forward import ForwardSendError, send_forward_msg
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary

PLUGIN_ID = "pixiv_id_fetcher"
_PID_PATTERN = re.compile(
    r"(?:(?:https?://)?(?:www\.)?pixiv\.net/(?:en/)?artworks/)?(\d+)"
)
_HTTP_ERROR_TEXTS = {
    404: "作品不存在、已删除或当前账号无权限查看",
    403: "当前账号无权限查看该作品，或代理/防盗链请求被 Pixiv 拒绝",
    429: "Pixiv 请求过于频繁，请稍后再试",
}

pixiv_cmd = on_command(
    "pixiv",
    aliases={"pid", "p站图"},
    rule=command_boundary & switch_rule(PLUGIN_ID),
    priority=5,
    block=True,
)


def _info(illust: Illust) -> str:
    return f"{illust.title}\n{illust.author}\npid: {illust.pid}"


async def _finish_failed(pid: int, e: Exception) -> NoReturn:
    """在 except 块里调用：按异常类型记日志，并回复用户"""
    if isinstance(e, HttpError):
        reply = _HTTP_ERROR_TEXTS.get(e.status_code, "获取 Pixiv 图片失败，请稍后再试")
    elif isinstance(e, (aiohttp.ClientError, asyncio.TimeoutError)):
        reply = "连接 Pixiv 失败，请检查网络或代理"
    elif isinstance(e, DownloadTooLarge):
        reply = "图片或动图文件过大，已停止发送"
    elif isinstance(e, PixivError):
        reply = str(e)
    else:
        logger.exception(f"[pixiv_id_fetcher] 未预期的错误 pid={pid}")
        await pixiv_cmd.finish("获取 Pixiv 图片失败，请稍后再试")
    logger.warning(f"[pixiv_id_fetcher] 请求失败 pid={pid}: {type(e).__name__}: {e}")
    await pixiv_cmd.finish(reply)


async def _send_images(
    bot: Bot, event: MessageEvent, illust: Illust, images: list[MessageSegment]
) -> bool:
    """被拒返回 False；转发超时结果未知时按已发出算，不补发"""
    try:
        if len(illust.urls) > 1 or illust.x_restrict:
            items = [_info(illust), *images]
            if len(images) < len(illust.urls):
                items.append(
                    f"多页作品，仅发送前 {len(images)} / {len(illust.urls)} 页"
                )
            await send_forward_msg(
                bot, event, items=items, fallback_on_action_failed=False
            )
        else:
            await bot.send(event, MessageSegment.text(_info(illust) + "\n") + images[0])
    except (ActionFailed, NetworkError, ForwardSendError) as e:
        logger.warning(f"[pixiv_id_fetcher] 图片发送被拒 pid={illust.pid}: {e}")
        return False
    return True


async def _send_links(
    bot: Bot, event: MessageEvent, illust: Illust, urls: list[str]
) -> None:
    lines = [f"图片发送失败，以下为图片链接\n{_info(illust)}"]
    if illust.is_ugoira:
        lines.append(f"作品链接：https://www.pixiv.net/artworks/{illust.pid}")
    else:
        lines += [f"第 {i} 页：{url}" for i, url in enumerate(urls, 1)]
        if len(urls) < len(illust.urls):
            lines.append(f"仅列出前 {len(urls)} / {len(illust.urls)} 页")
    try:
        await send_forward_msg(bot, event, items=lines, fallback_on_action_failed=False)
    except ForwardSendError as e:
        logger.warning(f"[pixiv_id_fetcher] 链接转发也被拒 pid={illust.pid}: {e}")
        await pixiv_cmd.finish("图片和链接都发送失败")


@pixiv_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    remaining = cooldown_remaining(PLUGIN_ID, event)
    if remaining > 0:
        await pixiv_cmd.finish(f"功能冷却中，请等待 {remaining} 秒", at_sender=True)

    match = _PID_PATTERN.fullmatch(args.extract_plain_text().strip())
    if not match:
        await pixiv_cmd.finish("格式：pixiv <pid>，也支持 Pixiv 作品链接")
    pid = int(match.group(1))

    await pixiv_cmd.send("在找了在找了...")

    try:
        illust = await fetch_illust(pid)
    except Exception as e:
        await _finish_failed(pid, e)

    if illust.x_restrict == 2 and not config.allow_r18g:
        await pixiv_cmd.finish("该作品为 R18G，当前配置未允许发送")
    if illust.x_restrict == 1 and not config.allow_r18:
        await pixiv_cmd.finish("该作品为 R18，当前配置未允许发送")

    urls = illust.urls[: config.max_pages]
    try:
        if illust.is_ugoira:
            images = [image_segment(await fetch_ugoira_gif(pid))]
        else:
            images = [image_segment(await download_image(url)) for url in urls]
    except Exception as e:
        await _finish_failed(pid, e)

    if not await _send_images(bot, event, illust, images):
        await _send_links(bot, event, illust, urls)
    mark_cooldown(PLUGIN_ID, event)
