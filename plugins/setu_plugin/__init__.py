from __future__ import annotations

import asyncio
import time
from typing import Optional, List, Dict, Any

import httpx

from nonebot import on_command, get_driver, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as localstore

from ..plugin_manager.enable import is_plugin_enabled
from ..plugin_manager.cd_manager import check_cd, update_cd

from .client import SetuClient

__plugin_name__ = "涩图（lzst）"
__plugin_usage__ = """
发送 lzst 获取一张随机图片
发送 lzst <tag> 根据 tag 获取图片（例如：lzst 白丝）

输出方式：合并转发（Title/Pid + 图片/链接）

本插件的开关与CD完全由 plugin_manager 管理：
- 启用/禁用：启用 setu_plugin / 禁用 setu_plugin
- CD：启用CD setu_plugin <秒> / 禁用CD setu_plugin
""".strip()

CACHE_TTL_DAYS = 7
CACHE_CLEAN_INTERVAL_SECONDS = 24 * 60 * 60  # daily
_cache_clean_task: Optional[asyncio.Task] = None

_lzst_semaphore: Optional[asyncio.Semaphore] = None


async def _cleanup_cache_once() -> None:
    """Remove cache files older than CACHE_TTL_DAYS in setu_plugin cache dir."""
    cache_dir = localstore.get_plugin_cache_file("_cache_dir_placeholder").parent
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


@get_driver().on_startup
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

    _cache_clean_task = asyncio.create_task(_loop())


@get_driver().on_shutdown
async def _stop_setu_cache_cleaner():
    global _cache_clean_task
    if _cache_clean_task:
        _cache_clean_task.cancel()
        _cache_clean_task = None


PLUGIN_ID = "setu_plugin"

lzst = on_command("lzst", aliases={"来张涩图"}, priority=5, block=True)


def _get_cfg(name: str, default):
    """
    Read from NoneBot global config.

    Supports both:
    - lowercase: setu_plugin_timeout
    - uppercase: SETU_PLUGIN_TIMEOUT
    """
    cfg = get_driver().config
    return getattr(cfg, name, getattr(cfg, name.upper(), default))


def _get_lzst_semaphore() -> asyncio.Semaphore:
    """Limit concurrent lzst executions to avoid bandwidth/IO spikes."""
    global _lzst_semaphore
    if _lzst_semaphore is None:
        concurrency = int(_get_cfg("setu_plugin_concurrency", 2))
        if concurrency < 1:
            concurrency = 1
        _lzst_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[setu_plugin] concurrency limit = {concurrency}")
    return _lzst_semaphore


@lzst.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = str(event.get_user_id())

    # --- plugin_manager: 开关 + CD（仅群聊生效） ---
    group_id: Optional[str] = None
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)

        if not is_plugin_enabled(PLUGIN_ID, group_id, user_id):
            await lzst.finish()

        remaining = check_cd(PLUGIN_ID, group_id, user_id)
        if remaining > 0:
            await lzst.finish(f"功能冷却中，请等待 {remaining} 秒", at_sender=True)

    # 给用户即时反馈（失败不影响主流程）
    try:
        await bot.send(event, "在找了在找了…")
    except Exception:
        pass

    # --- 参数解析：lzst <tag> ---
    tag = args.extract_plain_text().strip() or None

    # --- HTTP / API 配置（可选） ---
    timeout = float(_get_cfg("setu_plugin_timeout", 20.0))
    proxy = _get_cfg("setu_plugin_proxy", None)
    r18 = int(_get_cfg("setu_plugin_r18", 0))
    reverse_proxy_domain = _get_cfg("setu_plugin_reverse_proxy_domain", None)

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
        max_refetch = int(_get_cfg("setu_plugin_refetch_404_times", 3))
        if max_refetch < 1:
            max_refetch = 1

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
                logger.warning(f"[setu_plugin] empty result. tag={tag!r}")
                await lzst.finish("没有合适的涩图呢...")

            cache_path = None
            img_seg = None

            try:
                img_bytes = await client.download_image(result.display_url)
                cache_path = localstore.get_plugin_cache_file(f"setu_{result.pid}.jpg")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(img_bytes)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response else None
                if status == 404:
                    logger.warning(
                        f"[setu_plugin] image 404, refetch another one ({attempt + 1}/{max_refetch}) url={result.display_url}"
                    )

                    # 如果 display_url 是反代域名导致 404，尝试用原始 url 再下载一次
                    if result.url and result.url != result.display_url:
                        try:
                            img_bytes = await client.download_image(result.url)
                            cache_path = localstore.get_plugin_cache_file(
                                f"setu_{result.pid}.jpg"
                            )
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            cache_path.write_bytes(img_bytes)
                        except httpx.HTTPStatusError as e2:
                            status2 = e2.response.status_code if e2.response else None
                            if status2 == 404:
                                logger.warning(
                                    f"[setu_plugin] original url also 404, refetch another one ({attempt + 1}/{max_refetch}) url={result.url}"
                                )
                            else:
                                logger.exception(
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
                            logger.exception(
                                f"[setu_plugin] cache image failed: {e2} url={result.url}"
                            )
                            break

                    # 如果本轮仍然没有 cache，则继续 refetch
                    if not (cache_path and cache_path.exists()):
                        if attempt < max_refetch - 1:
                            continue
                        await lzst.finish("抽到的图源已失效(404)，请重新发送 lzst")
                else:
                    logger.exception(
                        f"[setu_plugin] cache image failed: {e} url={result.display_url}"
                    )
            except ValueError as e:
                # 典型：image too large；这属于预期内降级，不打印 traceback
                logger.warning(f"[setu_plugin] skip cache image: {e} url={result.display_url}")
            except Exception as e:
                logger.exception(f"[setu_plugin] cache image failed: {e} url={result.display_url}")

            # 构造“图片消息段”：优先本地文件，其次 URL（注意：遇到 404 我们会提前 refetch，不会走到这里）
            if cache_path and cache_path.exists():
                img_seg = MessageSegment.image(f"file:///{cache_path.resolve().as_posix()}")
            elif result.display_url:
                img_seg = MessageSegment.image(result.display_url)

            break

        # result 在上面流程中确保非空
        info = f"Title: {result.title}\nPid: {result.pid}"

        # --- 合并转发 ---
        bot_info = await bot.get_login_info()
        bot_uin = str(bot_info.get("user_id", "0"))
        bot_nickname = str(bot_info.get("nickname", "bot"))

        def _mk_node(content: str) -> Dict[str, Any]:
            return {"type": "node", "data": {"name": bot_nickname, "uin": bot_uin, "content": content}}

        original_link = result.url or ""
        regular_link = result.display_url or ""
        original_link_text = f"原图链接：{original_link}" if original_link else ""
        fallback_link_text = f"图片链接：{regular_link or original_link}"

        # --- 1) 优先尝试：合并转发（Title/Pid + 图片/链接 + 原图链接） ---
        nodes: List[Dict[str, Any]] = [
            _mk_node(info),
            _mk_node(str(img_seg) if img_seg else fallback_link_text),
        ]
        if original_link_text:
            nodes.append(_mk_node(original_link_text))

        sent_ok = False
        try:
            if group_id:
                await bot.send_forward_msg(group_id=int(group_id), messages=nodes)
            else:
                await bot.send_private_forward_msg(user_id=int(user_id), messages=nodes)
            sent_ok = True
        except FinishedException:
            raise
        except Exception as e:
            logger.exception(f"[setu_plugin] send forward failed: {e}")

        # --- 2) 合并转发失败：fallback 到分开发图（先发 info，再发图；失败则发链接） ---
        if not sent_ok:
            try:
                await bot.send(event, info)
                if img_seg:
                    await bot.send(event, img_seg)
                else:
                    await bot.send(event, fallback_link_text)

                if original_link_text:
                    await bot.send(event, original_link_text)

                sent_ok = True
            except Exception as e:
                logger.exception(f"[setu_plugin] send split failed: {e}")

        # --- 3) 分开发图也失败：最后 fallback 到仅链接 ---
        if not sent_ok:
            try:
                await bot.send(event, original_link_text or fallback_link_text)
                sent_ok = True
            except Exception as e:
                logger.exception(f"[setu_plugin] send link failed: {e}")
                await lzst.finish("发不出图也发不出链接，请稍后再试")

        # --- plugin_manager: 更新CD（仅在成功回应后记录；仅群聊记录） ---
        if sent_ok and group_id:
            update_cd(PLUGIN_ID, group_id, user_id)

        await lzst.finish()
