# plugins/buaa_msm/msr.py
"""
MSR 联合输出编排（统计图 + 位置图）。

说明：
- 对外 orchestration 入口：`execute_msr_analysis`
- 纯渲染逻辑位于：`plugins/buaa_msm/renderers/msr.py`
  - generate_msr_summary_image_bytes
  - generate_msr_map_image_bytes
  - stitch_images_grid_memory（若外部需要也可复用）
"""

from __future__ import annotations

import io
from typing import Any, Callable, Dict, List, Tuple

import asyncio
import aiohttp
from PIL import Image
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from . import analysis
from .config import plugin_config
from .domain.models import UserDataContext
from .exceptions import AssetDownloadError, RenderError, SendError
from .infra.visit_history import get_duplicate_chars_for_latest
from .renderers.msr import (
    generate_msr_map_image_bytes,
    generate_msr_summary_image_bytes,
    stitch_images_grid_memory,
)
from .services.rip_asset_lite import rip_asset_lite

__all__ = [
    "execute_msr_analysis",
    "generate_msr_summary_image_bytes",
    "generate_msr_map_image_bytes",
    "stitch_images_grid_memory",
]

SendFunc = Callable[[str], Any]


async def execute_msr_analysis(
    *,
    bot,
    user_ctx: UserDataContext,
    event_user_id: int,
    send_func: SendFunc,
) -> bool:
    """
    MSR 核心执行逻辑：
    - 生成 summary 图 + map 图
    - 以 bytes 形式发送

    参数说明：
    - send_func: 通常为 matcher.send，用于发“处理中”提示
    """
    decrypted_data = user_ctx.decrypted_data
    parsed_maps = user_ctx.parsed_maps

    highlight_characters = get_duplicate_chars_for_latest(user_ctx.user_id)
    visiting_characters = analysis.get_visiting_group_counts(decrypted_data)
    owned_music_records = analysis.parse_owned_music_records(decrypted_data)
    analysis_data = analysis.aggregate_materials(parsed_maps)

    sent_any = False

    # 收集所有唱片 record_id 并预下载封面
    all_record_ids: List[str] = []
    for summary in analysis_data.values():
        for (category, item_id_str), _ in (summary or {}).items():
            if category == "mysekai_music_record":
                all_record_ids.append(str(item_id_str))
    all_record_ids = list(set(all_record_ids))

    jacket_cache: Dict[str, Image.Image] = {}
    jacket_urls = analysis.get_all_needed_jacket_urls(all_record_ids)
    if jacket_urls:
        jacket_cache = await _fetch_jacket_images(jacket_urls)

    # 预取 mysekai 动态 icon（供渲染线程直接走本地缓存读取）
    try:
        await rip_asset_lite.prefetch_from_analysis_data(analysis_data)
    except FinishedException:
        raise
    except Exception as e:
        logger.warning(f"MySekai 动态 icon 预取失败，将回退静态资源: {e}")

    # 预取 harvest fixture 本体材质图（供位置图点位 marker 使用）
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

    # 并行触发渲染（渲染本身是同步 + CPU 密集，使用线程池提升总耗时表现）
    await send_func("正在生成统计图与位置图...")

    summary_task = asyncio.create_task(
        asyncio.to_thread(
            generate_msr_summary_image_bytes,
            analysis_data=analysis_data,
            visiting_characters=visiting_characters,
            owned_music_records=owned_music_records,
            highlight_characters=highlight_characters,
            jacket_cache=jacket_cache,
        )
    )
    map_task = asyncio.create_task(asyncio.to_thread(generate_msr_map_image_bytes, parsed_maps=parsed_maps))

    # 发送顺序保持稳定：先 summary 后 map，但生成是并行的
    try:
        summary_bytes = await summary_task
        await bot.send_private_msg(user_id=event_user_id, message=MessageSegment.image(summary_bytes))
        sent_any = True
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"MSR summary 生成/发送失败: {e}")
        try:
            await send_func("统计图生成失败，已跳过。")
        except FinishedException:
            raise
        except Exception as send_err:
            raise SendError(f"统计图失败提示发送失败: {send_err}") from e

    try:
        map_bytes = await map_task
        await bot.send_private_msg(user_id=event_user_id, message=MessageSegment.image(map_bytes))
        sent_any = True
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"MSR map 生成/发送失败: {e}")
        try:
            await send_func("位置图生成失败，已跳过。")
        except FinishedException:
            raise
        except Exception as send_err:
            raise SendError(f"位置图失败提示发送失败: {send_err}") from e

    try:
        if sent_any:
            await send_func("分析结果发送完毕。")
        else:
            await send_func("抱歉，无法生成任何结果。")
    except FinishedException:
        raise
    except Exception as e:
        raise SendError(f"发送完成消息失败: {e}") from e

    return sent_any


def _is_retryable_http_status(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500


def _is_retryable_exception(exc: Exception) -> bool:
    return isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientError))


async def _fetch_jacket_images(
    url_map: Dict[str, str],
    timeout: float | None = None,
) -> Dict[str, Image.Image]:
    """
    批量异步下载封面图片。
    返回 {mysekai_record_id: PIL.Image} 字典，仅包含成功下载的。
    单张下载失败时静默跳过（fallback 到默认图标由渲染层处理）。
    """
    result: Dict[str, Image.Image] = {}
    if not url_map:
        return result

    total_timeout = float(timeout if timeout is not None else plugin_config.jacket_download_timeout)
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
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=total_timeout)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            img = Image.open(io.BytesIO(data)).convert("RGBA")
                            result[record_id] = img
                            return

                        logger.debug(f"封面下载失败 [{record_id}]: HTTP {resp.status} (attempt {attempt}/{max_attempts})")
                        if not _is_retryable_http_status(resp.status) or attempt >= max_attempts:
                            return
                except Exception as e:
                    logger.debug(f"封面下载失败 [{record_id}]: {e} (attempt {attempt}/{max_attempts})")
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
        logger.warning(f"封面批量下载出错: {e}")
        raise AssetDownloadError(str(e)) from e

    ok_cnt = len(result)
    logger.info(f"封面下载完成: {ok_cnt}/{len(url_map)} 张成功")
    if ok_cnt == 0 and url_map:
        raise RenderError("封面下载全部失败")
    return result
