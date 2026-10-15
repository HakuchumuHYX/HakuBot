# plugins/buaa_msm/services/msr_service.py
"""
MSR 分析服务（完整编排）：
- 获取用户上下文 → 解析数据 → 预取资源 → 并行渲染 → 结果图落盘 → 发送结果
- render_msr 供网页复用（只渲染落盘、不发送）
- 统一的 jacket 封面下载与 HTTP 重试逻辑
"""

from __future__ import annotations
from core.lifecycle import runtime, on_plugin_startup, on_plugin_shutdown

import asyncio
import io
from typing import Any, Callable

import aiohttp
from PIL import Image
from nonebot.exception import FinishedException
from nonebot import logger
from utils.json_io import atomic_write_bytes
from utils.onebot.media import image_segment

from plugins.buaa_msm import analysis
from plugins.buaa_msm.config import plugin_config, with_site_link
from plugins.buaa_msm.domain.models import UserDataContext
from plugins.buaa_msm.exceptions import (
    AssetDownloadError,
    DataLoadError,
    RenderError,
    SendError,
)
from plugins.buaa_msm.infra.visit_history import get_duplicate_chars_for_latest
from plugins.buaa_msm.renderers.msr import (
    generate_msr_map_image_bytes,
    generate_msr_summary_image_bytes,
)
from plugins.buaa_msm.services.ingest_service import RESULT_FILES
from plugins.buaa_msm.services.rip_asset_lite import rip_asset_lite
from plugins.buaa_msm.services.user_data_service import get_user_context

SendFunc = Callable[[str], Any]


# ============== HTTP 重试工具 ==============


def _is_retryable_http_status(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError))


# ============== 封面下载 ==============


async def _fetch_jacket_images(
    url_map: dict[str, str],
    timeout: float | None = None,
) -> dict[str, Image.Image]:
    """
    批量异步下载封面图片。
    返回 {mysekai_record_id: PIL.Image} 字典，仅包含成功下载的。
    """
    result: dict[str, Image.Image] = {}
    if not url_map:
        return result

    total_timeout = float(
        timeout if timeout is not None else plugin_config.jacket_download_timeout
    )
    retries = max(0, int(plugin_config.jacket_download_retries))
    backoff = max(0.0, float(plugin_config.jacket_retry_backoff_seconds))
    concurrency = max(1, int(plugin_config.jacket_download_concurrency))
    max_attempts = retries + 1
    sem = asyncio.Semaphore(concurrency)

    async def _download_one(
        session: aiohttp.ClientSession,
        record_id: str,
        url: str,
    ) -> None:
        async with sem:
            for attempt in range(1, max_attempts + 1):
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=total_timeout)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            img = Image.open(io.BytesIO(data)).convert("RGBA")
                            result[record_id] = img
                            return

                        logger.debug(
                            f"封面下载失败 [{record_id}]: HTTP {resp.status} (attempt {attempt}/{max_attempts})"
                        )
                        if (
                            not _is_retryable_http_status(resp.status)
                            or attempt >= max_attempts
                        ):
                            return
                except Exception as e:
                    logger.debug(
                        f"封面下载失败 [{record_id}]: {e} (attempt {attempt}/{max_attempts})"
                    )
                    if not _is_retryable_exception(e) or attempt >= max_attempts:
                        return

                if backoff > 0:
                    await asyncio.sleep(backoff * (2 ** (attempt - 1)))

    try:
        async with aiohttp.ClientSession() as session:
            tasks = [_download_one(session, rid, url) for rid, url in url_map.items()]
            await asyncio.gather(*tasks)
    except FinishedException:
        raise
    except Exception as e:
        raise AssetDownloadError(str(e)) from e

    ok_cnt = len(result)
    logger.debug(f"封面下载完成: {ok_cnt}/{len(url_map)} 张成功")
    if ok_cnt == 0 and url_map:
        raise RenderError("封面下载全部失败")
    return result


# ============== MSR 核心执行 ==============


async def render_msr(user_ctx: UserDataContext) -> tuple[bytes | None, bytes | None]:
    """
    生成统计图和位置图，落盘到用户输出目录（网页从这里读图），并返回 (summary, map) 的 bytes。
    单张失败时对应位置为 None（已记日志）；封面全部下载失败等整体错误照常抛出。
    """
    decrypted_data = user_ctx.decrypted_data
    parsed_maps = user_ctx.parsed_maps

    highlight_characters = get_duplicate_chars_for_latest(user_ctx.user_id)
    visiting_characters = analysis.get_visiting_group_counts(decrypted_data)
    owned_music_records = analysis.parse_owned_music_records(decrypted_data)
    analysis_data = analysis.aggregate_materials(parsed_maps)

    # 收集所有唱片 record_id 并预下载封面
    all_record_ids: list[str] = []
    for summary in analysis_data.values():
        for (category, item_id_str), _ in (summary or {}).items():
            if category == "mysekai_music_record":
                all_record_ids.append(str(item_id_str))
    all_record_ids = list(set(all_record_ids))

    jacket_cache: dict[str, Image.Image] = {}
    jacket_urls = analysis.get_all_needed_jacket_urls(all_record_ids)
    if jacket_urls:
        jacket_cache = await _fetch_jacket_images(jacket_urls)

    # 预取 mysekai 动态 icon
    try:
        await rip_asset_lite.prefetch_from_analysis_data(analysis_data)
    except FinishedException:
        raise
    except Exception as e:
        logger.warning(f"MySekai 动态 icon 预取失败，将回退静态资源: {e}")

    # 预取 harvest fixture 本体材质图
    try:
        fixture_ids = {
            int(point.get("fixtureId"))
            for points in parsed_maps.values()
            for point in (points or [])
            if point.get("fixtureId") is not None
        }
        if fixture_ids:
            await rip_asset_lite.prefetch_harvest_fixture_icons(fixture_ids)
    except FinishedException:
        raise
    except Exception as e:
        logger.warning(f"MySekai harvest fixture icon 预取失败，将回退圆点标记: {e}")

    # 并行渲染
    summary_task = runtime.spawn(
        asyncio.to_thread(
            generate_msr_summary_image_bytes,
            analysis_data=analysis_data,
            visiting_characters=visiting_characters,
            owned_music_records=owned_music_records,
            highlight_characters=highlight_characters,
            jacket_cache=jacket_cache,
        ),
        name="buaa_msm",
    )
    map_task = runtime.spawn(
        asyncio.to_thread(generate_msr_map_image_bytes, parsed_maps=parsed_maps),
        name="buaa_msm",
    )

    results: list[bytes | None] = []
    for kind, task in (("summary", summary_task), ("map", map_task)):
        out_path = user_ctx.user_output_dir / RESULT_FILES[kind]
        try:
            data = await task
        except Exception:
            logger.opt(exception=True).warning(f"MSR {kind} 生成失败")
            out_path.unlink(missing_ok=True)
            results.append(None)
            continue
        await asyncio.to_thread(atomic_write_bytes, out_path, data)
        results.append(data)

    return results[0], results[1]


async def _execute_msr_analysis(
    *,
    bot: Any,
    user_ctx: UserDataContext,
    event_user_id: int,
    send_func: SendFunc,
) -> bool:
    """
    QQ 私聊：生成 summary 图 + map 图，以 bytes 形式发送
    """
    await send_func("正在生成统计图与位置图...")
    images = await render_msr(user_ctx)

    # 发送顺序保持稳定：先 summary 后 map
    sent_any = False
    for image_bytes, label in zip(images, ("统计图", "位置图")):
        if image_bytes is None:
            await _send_or_raise(send_func, f"{label}生成失败，已跳过。")
            continue
        try:
            await bot.send_private_msg(
                user_id=event_user_id, message=image_segment(image_bytes)
            )
            sent_any = True
        except FinishedException:
            raise
        except Exception:
            logger.opt(exception=True).warning(f"MSR {label}发送失败")
            await _send_or_raise(send_func, f"{label}发送失败，已跳过。")

    await _send_or_raise(
        send_func,
        with_site_link("分析结果发送完毕。") if sent_any else "抱歉，无法生成任何结果。",
    )
    return sent_any


async def _send_or_raise(send_func: SendFunc, text: str) -> None:
    try:
        await send_func(text)
    except FinishedException:
        raise
    except Exception as e:
        raise SendError(f"提示消息发送失败（{text}）: {e}") from e


# ============== 对外接口 ==============


async def run_msr(
    *, bot: Any, user_id: str, event_user_id: int, send_func: SendFunc
) -> bool:
    """
    MSR 完整流程：获取用户上下文 → 执行分析渲染 → 发送结果。
    供 handlers 直接调用。
    """
    ctx_res = await get_user_context(user_id)
    if not ctx_res.ok or not ctx_res.ctx:
        raise DataLoadError(ctx_res.error or "unknown error")

    try:
        ok = await _execute_msr_analysis(
            bot=bot,
            user_ctx=ctx_res.ctx,
            event_user_id=event_user_id,
            send_func=send_func,
        )
        if not ok:
            raise RenderError("render returned false")
        return True
    except FinishedException:
        raise
    except Exception as e:
        logger.exception("MSR 执行失败")
        try:
            await send_func("抱歉，分析生成失败。")
        except FinishedException:
            raise
        except Exception as send_err:
            raise SendError(str(send_err)) from e
        return False
