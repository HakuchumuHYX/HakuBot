"""图片下载与处理模块"""
import asyncio
import random
import io
import os
from pathlib import Path
from typing import Optional
from PIL import Image
import httpx
from nonebot.log import logger


async def download_image(url: str, timeout: int = 15) -> Image.Image:
    """下载图片并返回 PIL Image 对象"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")


def load_local_image(path: Path) -> Optional[Image.Image]:
    """从本地路径加载图片，不存在或失败返回 None"""
    if not path.exists():
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception as e:
        logger.warning(f"[猜卡面] 加载本地图片失败 {path}: {e}")
        return None


async def download_image_to_file(url: str, dest: Path, timeout: int = 30) -> bool:
    """下载图片并保存到本地文件，返回是否成功"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return True
    except Exception as e:
        logger.debug(f"[猜卡面] 下载失败 {url}: {e}")
        return False


async def batch_download_images(
    tasks: list,
    concurrency: int = 5,
    progress_callback=None,
) -> dict:
    """
    批量下载图片。
    tasks: [(card, after_training, url, local_path), ...]
    progress_callback: async callable(done, total, skipped, failed) 每批完成后调用
    返回: {"total": N, "success": N, "skipped": N, "failed": N, "failed_urls": [...]}
    """
    sem = asyncio.Semaphore(concurrency)
    result = {"total": len(tasks), "success": 0, "skipped": 0, "failed": 0, "failed_urls": []}
    done_count = 0

    async def _download_one(url: str, local_path: Path):
        nonlocal done_count
        # 跳过已存在的文件
        if local_path.exists() and local_path.stat().st_size > 0:
            result["skipped"] += 1
            done_count += 1
            return

        async with sem:
            ok = await download_image_to_file(url, local_path)
            if ok:
                result["success"] += 1
            else:
                result["failed"] += 1
                result["failed_urls"].append(url)
            done_count += 1

    # 分批执行并报告进度
    batch_size = 50
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        coros = [_download_one(url, local_path) for _, _, url, local_path in batch]
        await asyncio.gather(*coros)

        if progress_callback:
            await progress_callback(done_count, result["total"], result["success"], result["skipped"], result["failed"])

    return result


def get_dir_size_mb(path: Path) -> float:
    """计算目录总大小（MB）"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = Path(dirpath) / f
            total += fp.stat().st_size
    return total / (1024 * 1024)


def random_crop_image(
    image: Image.Image,
    rate_min: float = 0.15,
    rate_max: float = 0.25,
) -> Image.Image:
    """
    随机裁剪图片的一小块区域
    rate_min/rate_max: 裁剪区域占原图宽高的比例范围
    """
    w, h = image.size
    w_rate = random.uniform(rate_min, rate_max)
    h_rate = random.uniform(rate_min, rate_max)
    w_crop = int(w * w_rate)
    h_crop = int(h * h_rate)
    x = random.randint(0, w - w_crop)
    y = random.randint(0, h - h_crop)
    return image.crop((x, y, x + w_crop, y + h_crop))


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    """将 PIL Image 转为 bytes"""
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()
