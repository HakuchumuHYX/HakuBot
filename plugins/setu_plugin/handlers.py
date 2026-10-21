from __future__ import annotations
from utils.paths import PluginPaths
from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown

import asyncio
import time
from typing import Optional, Any

from nonebot import on_command, get_driver, logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot.params import CommandArg

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from core.settings import ConfigModel, load_config
from pydantic import Field

from plugins.setu_plugin.client import SetuClient
from utils.logging import get_exc_desc
from utils.network.http import HttpError
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from utils.onebot.forward import send_forward_msg

CACHE_TTL_DAYS = 7
CACHE_CLEAN_INTERVAL_SECONDS = 24 * 60 * 60  # daily
_cache_clean_task: Optional[asyncio.Task] = None

_lzst_semaphore: Optional[asyncio.Semaphore] = None


async def _cleanup_cache_once() -> None:
    """Remove cache files older than CACHE_TTL_DAYS in setu_plugin cache dir."""
    cache_dir = (PluginPaths("setu_plugin").cache / "_cache_dir_placeholder").parent
    cache_dir.mkdir(parents=True, exist_ok=True)

    expire_before = time.time() - CACHE_TTL_DAYS * 24 * 60 * 60
    removed = 0

    # 仅清理本插件生成的文件，避免误删未来可能写入 cache 的其他文件
    for p in cache_dir.glob("setu_*"):
        try:
            if not p.is_file():
                continue
            if p.stat().st_mtime >= expire_before:
                continue
            p.unlink(missing_ok=True)  # py3.9+
            removed += 1
        except Exception as e:
            logger.exception(f"[setu_plugin] cache cleanup failed: {e} file={p}")

    if removed:
        logger.info(f"[setu_plugin] cache cleanup removed {removed} files")


@on_plugin_startup(get_driver(), "setu_plugin")
async def _start_setu_cache_cleaner():
    global _cache_clean_task

    # 启动时先清一次，防止积压
    try:
        await _cleanup_cache_once()
    except Exception as e:
        logger.exception(f"[setu_plugin] startup cache cleanup failed: {e}")

    async def _loop():
        while True:
            await asyncio.sleep(CACHE_CLEAN_INTERVAL_SECONDS)
            try:
                await _cleanup_cache_once()
            except Exception as e:
                logger.exception(f"[setu_plugin] scheduled cache cleanup failed: {e}")

    _cache_clean_task = runtime.spawn(_loop(), name="setu_plugin")


@on_plugin_shutdown(get_driver(), "setu_plugin")
async def _stop_setu_cache_cleaner():
    global _cache_clean_task
    if _cache_clean_task:
        _cache_clean_task.cancel()
        _cache_clean_task = None


PLUGIN_ID = "setu_plugin"

lzst = on_command(
    "lzst",
    aliases={"来张涩图"},
    rule=command_boundary & switch_rule(PLUGIN_ID),
    priority=5,
    block=True,
)


class Config(ConfigModel):
    timeout: float
    proxy: Optional[str]
    r18: int
    reverse_proxy_domain: Optional[str]
    # 同时处理的请求数，取图、下载、发送都算在内
    concurrency: int = Field(ge=1)
    # 图片 URL 404 时最多重新抽几次
    refetch_404_times: int = Field(ge=1)


plugin_config = load_config(PLUGIN_ID, Config)


def _get_lzst_semaphore() -> asyncio.Semaphore:
    """Limit concurrent lzst executions to avoid bandwidth/IO spikes."""
    global _lzst_semaphore
    if _lzst_semaphore is None:
        concurrency = plugin_config.concurrency
        _lzst_semaphore = asyncio.Semaphore(concurrency)
        logger.debug(f"[setu_plugin] concurrency limit = {concurrency}")
    return _lzst_semaphore


@lzst.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.get_user_id())

    group_id: Optional[str] = None
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)

    remaining = cooldown_remaining(PLUGIN_ID, event)
    if remaining > 0:
        await lzst.finish(f"功能冷却中，请等待 {remaining} 秒", at_sender=True)

    # 给用户即时反馈（失败不影响主流程）
    try:
        await bot.send(event, "在找了在找了…")
    except Exception as e:
        logger.warning(f"[setu_plugin] 发送等待提示失败: {get_exc_desc(e)}")

    # --- 参数解析：lzst <tag> ---
    tag = args.extract_plain_text().strip() or None

    # --- HTTP / API 配置（可选） ---
    timeout = plugin_config.timeout
    proxy = plugin_config.proxy
    r18 = plugin_config.r18
    reverse_proxy_domain = plugin_config.reverse_proxy_domain

    def _mask_proxy(p: Any) -> str:
        if not p:
            return "None"
        s = str(p)
        # mask credentials if present: scheme://user:pass@host -> scheme://user:***@host
        if "://" in s and "@" in s:
            scheme, rest = s.split("://", 1)
            cred, host = rest.split("@", 1)
            if ":" in cred:
                user = cred.split(":", 1)[0]
                return f"{scheme}://{user}:***@{host}"
        return s

    logger.info(
        f"[setu_plugin] request start user={user_id} group={group_id} tag={tag!r} r18={r18} proxy={_mask_proxy(proxy)} reverse_proxy={reverse_proxy_domain!r}"
    )

    client = SetuClient(
        timeout=timeout,
        proxy=proxy,
        r18=r18,
        reverse_proxy_domain=reverse_proxy_domain,
    )

    # 并发限制：fetch + 下载 + 发送都放进 semaphore
    async with _get_lzst_semaphore():
        # --- 取图 + 404 自动换图（重新抽一张，不是重下同一张） ---
        # 说明：部分返回的图片 URL 可能已失效（404）。遇到 404 时重新 fetch 抽新的图。
        max_refetch = plugin_config.refetch_404_times

        result = None
        img_seg: Optional[MessageSegment] = None
        cache_path = None

        for attempt in range(max_refetch):
            try:
                result = await client.fetch(tag)
            except Exception as e:
                logger.exception(f"[setu_plugin] fetch failed: {e}")
                await lzst.finish("请求涩图失败，请稍后再试")

            if not result or not result.display_url:
                logger.info(f"[setu_plugin] empty result. tag={tag!r}")
                await lzst.finish("没有合适的涩图呢...")

            cache_path = None
            img_seg = None

            try:
                img_bytes = await client.download_image(result.display_url)
                cache_path = PluginPaths("setu_plugin").cache / f"setu_{result.pid}.jpg"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(img_bytes)
            except HttpError as e:
                if e.status_code == 404:
                    logger.info(
                        f"[setu_plugin] image 404, refetch another one ({attempt + 1}/{max_refetch}) url={result.display_url}"
                    )

                    # 如果 display_url 是反代域名导致 404，尝试用原始 url 再下载一次
                    if result.url and result.url != result.display_url:
                        try:
                            img_bytes = await client.download_image(result.url)
                            cache_path = (
                                PluginPaths("setu_plugin").cache
                                / f"setu_{result.pid}.jpg"
                            )
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            cache_path.write_bytes(img_bytes)
                        except HttpError as e2:
                            if e2.status_code == 404:
                                logger.info(
                                    f"[setu_plugin] original url also 404, refetch another one ({attempt + 1}/{max_refetch}) url={result.url}"
                                )
                            else:
                                logger.opt(exception=True).warning(
                                    f"[setu_plugin] cache image failed: {e2} url={result.url}"
                                )
                                # 非 404 错误：不继续换图，走后续降级逻辑
                                break
                        except ValueError as e2:
                            logger.warning(
                                f"[setu_plugin] skip cache image: {e2} url={result.url}"
                            )
                            break
                        except Exception as e2:
                            logger.opt(exception=True).warning(
                                f"[setu_plugin] cache image failed: {e2} url={result.url}"
                            )
                            break

                    # 如果本轮仍然没有 cache，则继续 refetch
                    if not (cache_path and cache_path.exists()):
                        if attempt < max_refetch - 1:
                            continue
                        logger.warning(
                            f"[setu_plugin] 连续 {max_refetch} 次图源 404，放弃 tag={tag!r}"
                        )
                        await lzst.finish("抽到的图源已失效(404)，请重新发送 lzst")
                else:
                    logger.opt(exception=True).warning(
                        f"[setu_plugin] cache image failed: {e} url={result.display_url}"
                    )
            except ValueError as e:
                # 典型：image too large；这属于预期内降级，不打印 traceback
                logger.warning(
                    f"[setu_plugin] skip cache image: {e} url={result.display_url}"
                )
            except Exception as e:
                logger.opt(exception=True).warning(
                    f"[setu_plugin] cache image failed: {e} url={result.display_url}"
                )

            # 构造“图片消息段”：优先本地文件，其次 URL（注意：遇到 404 我们会提前 refetch，不会走到这里）
            if cache_path and cache_path.exists():
                img_seg = image_segment(cache_path)
            elif result.display_url:
                img_seg = image_segment(result.display_url)

            break

        # result 在上面流程中确保非空
        info = f"Title: {result.title}\nPid: {result.pid}"

        original_link = result.url or ""
        regular_link = result.display_url or ""
        original_link_text = f"原图链接：{original_link}" if original_link else ""
        fallback_link_text = f"图片链接：{regular_link or original_link}"

        # --- 1) 优先尝试：合并转发（Title/Pid + 图片/链接 + 原图链接） ---
        items = [info, img_seg if img_seg else fallback_link_text]
        if original_link_text:
            items.append(original_link_text)

        sent_ok = False
        try:
            await send_forward_msg(bot, event, items=items)
            sent_ok = True
        except FinishedException:
            raise
        except Exception as e:
            logger.opt(exception=True).warning(
                f"[setu_plugin] send forward failed: {e}"
            )

        # 公共转发器已处理明确失败时的逐条补发；仍失败则最后仅发链接。
        if not sent_ok:
            try:
                await bot.send(event, original_link_text or fallback_link_text)
                sent_ok = True
            except Exception as e:
                logger.exception(f"[setu_plugin] send link failed: {e}")
                await lzst.finish("发不出图也发不出链接，请稍后再试")

        # 成功回应后才扣 CD
        if sent_ok:
            mark_cooldown(PLUGIN_ID, event)

        await lzst.finish()
