"""入库与生成分析图：QQ 私聊上传和网页上传共用"""

import asyncio
from pathlib import Path

from nonebot import logger

from core.lifecycle import runtime
from utils.json_io import atomic_write_bytes
from plugins.buaa_msm import store
from plugins.buaa_msm.assets import prefetch
from plugins.buaa_msm.gamedata import MsmError, analyze, decrypt, visit_group_ids
from plugins.buaa_msm.render import render_map, render_summary


async def ingest(qq: str, saved_path: Path) -> None:
    """新数据包落盘后：删掉旧数据包和旧结果图，试解密，记录来访角色。

    先删旧结果图，免得这次解密或生成失败时网页还显示上一份数据的图。
    解密失败抛 MsmError。
    """
    store.drop_previous(qq, saved_path)
    decrypted = await asyncio.to_thread(decrypt, saved_path)
    group_ids = visit_group_ids(decrypted)
    if group_ids:
        store.record_visits(qq, group_ids)
        logger.debug(f"已记录用户 {qq} 的 {len(group_ids)} 个来访角色ID")


async def generate(qq: str) -> tuple[bytes | None, bytes | None]:
    """用最新数据包生成 (统计图, 位置图) 并落盘。

    单张失败时对应位置为 None（已记日志）；数据本身有问题抛 MsmError。
    """
    path = store.latest_upload(qq)
    if path is None:
        raise MsmError("您还没有上传过文件，请先使用 'buaa上传文件' 命令。")
    if path.suffix.lower() != ".bin":
        raise MsmError("您上传的文件不是 .bin 格式，请上传正确的 mysekai 包体文件。")

    data = analyze(await asyncio.to_thread(decrypt, path))
    jackets = await prefetch(data)
    repeated = store.repeated_visits(qq)

    summary_task = runtime.spawn(
        asyncio.to_thread(render_summary, data, jackets, repeated), name="buaa_msm"
    )
    map_task = runtime.spawn(asyncio.to_thread(render_map, data), name="buaa_msm")

    results: list[bytes | None] = []
    for kind, task in (("summary", summary_task), ("map", map_task)):
        out_path = store.result_path(qq, kind)
        try:
            image = await task
        except Exception:
            logger.opt(exception=True).warning(f"MSR {kind} 生成失败")
            out_path.unlink(missing_ok=True)
            results.append(None)
            continue
        await asyncio.to_thread(atomic_write_bytes, out_path, image)
        results.append(image)
    return results[0], results[1]
