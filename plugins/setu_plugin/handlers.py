"""lzst：从 lolicon API 抽一张图，合并转发发出去"""

import asyncio

import aiohttp
from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg
from pydantic import Field

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from core.settings import ConfigModel, load_config
from utils.network.http import (
    HttpError,
    download_bytes,
    get_client_session,
    get_effective_proxy,
)
from utils.onebot.forward import send_forward_msg
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary

PLUGIN_ID = "setu_plugin"
API_URL = "https://api.lolicon.app/setu/v2"


class Config(ConfigModel):
    timeout: float
    proxy: str | None
    r18: int
    # 库里有些作品已经被删，图片 404，这时重新抽一张
    refetch_404_times: int = Field(ge=1)


config = load_config(PLUGIN_ID, Config)

lzst = on_command(
    "lzst",
    aliases={"来张涩图"},
    rule=command_boundary & switch_rule(PLUGIN_ID),
    priority=5,
    block=True,
)


async def _fetch(tag: str) -> dict | None:
    # 不带 size 时只返回 original；regular 是长边 1200 左右的 jpg，发这个
    params = [("r18", config.r18), ("size", "regular"), ("size", "original")]
    if tag:
        params.append(("tag", tag))
    async with get_client_session().get(
        API_URL,
        params=params,
        proxy=get_effective_proxy(config.proxy),
        timeout=aiohttp.ClientTimeout(total=config.timeout),
    ) as response:
        response.raise_for_status()
        data = (await response.json())["data"]
    return data[0] if data else None


async def _fetch_with_image(tag: str) -> tuple[dict, bytes] | None:
    """抽到的图连续 404 时返回 None"""
    for _ in range(config.refetch_404_times):
        item = await _fetch(tag)
        if item is None:
            await lzst.finish("没有合适的涩图呢...")
        try:
            image = await download_bytes(
                item["urls"]["regular"], proxy=config.proxy, timeout=config.timeout
            )
            return item, image
        except HttpError as e:
            if e.status_code != 404:
                raise
            logger.info(f"[setu_plugin] 图片已失效，重新抽一张 pid={item['pid']}")
    return None


@lzst.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    remaining = cooldown_remaining(PLUGIN_ID, event)
    if remaining > 0:
        await lzst.finish(f"功能冷却中，请等待 {remaining} 秒", at_sender=True)
    await lzst.send("在找了在找了…")

    try:
        result = await _fetch_with_image(args.extract_plain_text().strip())
    except (aiohttp.ClientError, asyncio.TimeoutError, HttpError) as e:
        logger.opt(exception=True).warning(f"[setu_plugin] 请求失败: {e}")
        await lzst.finish("请求涩图失败，请稍后再试")
    if result is None:
        await lzst.finish("抽到的图源已失效(404)，请重新发送 lzst")

    item, image = result
    await send_forward_msg(
        bot,
        event,
        items=[
            f"Title: {item['title']}\nPid: {item['pid']}",
            image_segment(image),
            f"原图链接：{item['urls']['original']}",
        ],
    )
    mark_cooldown(PLUGIN_ID, event)
