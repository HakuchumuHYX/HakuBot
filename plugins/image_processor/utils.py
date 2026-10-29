import asyncio
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from nonebot import logger
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from utils.network.http import DownloadTooLarge, HttpError, temporary_download
from utils.onebot.media import outbound_media_path

# 静态图和 GIF 共用一个上限：QQ 图片链接没有扩展名，下载前分不出是不是 GIF
IMAGE_PROCESSOR_MAX_IMAGE_BYTES = 50 * 1024 * 1024
IMAGE_PROCESSOR_MAX_VIDEO_BYTES = 200 * 1024 * 1024
IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT = 60
IMAGE_PROCESSOR_VIDEO_DOWNLOAD_TIMEOUT = 180
# 帧数 × 宽 × 高 的上限。GIF 压缩率高，50MB 以内的文件解码成 RGBA 能到几 GB，
# 曾把本机（2 核、空闲内存约 1GB）打到 OOM；处理时峰值约两份 RGBA，即 400～500MB
IMAGE_PROCESSOR_MAX_PIXELS = 50_000_000
GIF_MIN_DURATION_MS = 20

# 图片处理和抠图模型推理（峰值约 1GB）一次只跑一个，后来的排队：
# 2 核机器上并发也快不了多少，内存却成倍涨
HEAVY_LOCK = asyncio.Lock()


class UserInputError(Exception):
    """用户给的素材不合要求（格式、大小、时长等）：文案直接回给用户，不算故障"""


class ProcessingError(Exception):
    """下载失败、子进程超时这类故障，原因可以直接告诉用户"""


@asynccontextmanager
async def download_temp(
    url: str, *, ext: str, max_bytes: int, timeout: float, **options
):
    """下载到临时文件，退出时删除；下载层异常转成能直接回给用户的文案"""
    try:
        async with temporary_download(
            url,
            ext=ext,
            max_bytes=max_bytes,
            timeout=timeout,
            allowed_statuses=(200, 206),
            attempts=1,
            **options,
        ) as path:
            yield str(path)
    except DownloadTooLarge as e:
        raise UserInputError(f"文件超过 {max_bytes // 1024 // 1024}MB 上限") from e
    except HttpError as e:
        raise ProcessingError(f"下载失败（HTTP {e.status_code}）") from e
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise ProcessingError("下载失败（网络错误或超时）") from e


def _probe_image(image_path: str) -> bool:
    """返回是否动图；超出像素上限直接拒绝，不等解码时把内存吃满"""
    try:
        with Image.open(image_path) as img:
            frame_count = getattr(img, "n_frames", 1)
            width, height = img.size
    except UnidentifiedImageError as e:
        raise UserInputError("无法识别的图片格式") from e
    except Image.DecompressionBombError as e:
        raise UserInputError("图片尺寸太大，超出处理上限") from e
    if frame_count * width * height > IMAGE_PROCESSOR_MAX_PIXELS:
        raise UserInputError(
            f"图片太大（{frame_count} 帧 {width}×{height}），超出处理上限"
        )
    return frame_count > 1


@asynccontextmanager
async def download_image(url: str):
    """产出 (临时路径, 是否动图)"""
    async with download_temp(
        url,
        ext="img",
        max_bytes=IMAGE_PROCESSOR_MAX_IMAGE_BYTES,
        timeout=IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
    ) as path:
        yield path, await asyncio.to_thread(_probe_image, path)


async def run_heavy(func, *args):
    async with HEAVY_LOCK:
        return await asyncio.to_thread(func, *args)


def output_path(prefix: str, ext: str) -> Path:
    """结果直接写进共享目录，发图时不用再复制一份；发完由 handlers 删除"""
    return outbound_media_path(f"{prefix}_{os.urandom(4).hex()}.{ext}")


async def run_command(cmd: list[str], *, timeout: float, label: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ProcessingError(f"{label}超时（>{timeout:g} 秒）")
    if process.returncode != 0:
        logger.warning(f"{label}失败: {stderr.decode(errors='replace')[-1000:]}")
        raise ProcessingError(f"{label}失败")
    return stdout


def open_static_image(image_path: str) -> Image.Image:
    """手机照片的方向写在 EXIF 里，不转正的话处理完再丢掉 EXIF 方向就错了"""
    with Image.open(image_path) as img:
        return ImageOps.exif_transpose(img)


def convert_to_supported_mode(img: Image.Image) -> Image.Image:
    if img.mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode == "LA":
        return img.convert("RGBA")
    if img.mode not in ["RGB", "RGBA"]:
        return img.convert("RGB")
    return img


def _normalize_durations(durations: list[int], frame_count: int) -> list[int]:
    if not durations:
        return [100] * frame_count
    normalized = [max(1, int(d or 100)) for d in durations]
    if len(normalized) < frame_count:
        normalized.extend([normalized[-1]] * (frame_count - len(normalized)))
    return normalized[:frame_count]


def load_gif_frames(
    image_path: str | Path,
) -> tuple[list[Image.Image], list[int], dict]:
    frames: list[Image.Image] = []
    durations: list[int] = []
    meta: dict = {}

    with Image.open(image_path) as img:
        meta["loop"] = int(img.info.get("loop", 0) or 0)
        default_duration = int(img.info.get("duration", 100) or 100)
        for frame in ImageSequence.Iterator(img):
            durations.append(
                int(frame.info.get("duration", default_duration) or default_duration)
            )
            frames.append(frame.convert("RGBA").copy())

    return frames, _normalize_durations(durations, len(frames)), meta


def _has_transparency(frames: list[Image.Image]) -> bool:
    return any(frame.getextrema()[3][0] < 255 for frame in frames)


def _rgba_to_transparent_p(frame: Image.Image) -> Image.Image:
    # GIF 只有全透明/不透明：半透明边缘按 128 二值化并保留原色，
    # 先合到黑底再量化会让边缘变成一圈黑边
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    paletted = rgba.convert("RGB").convert(
        "P", palette=Image.Palette.ADAPTIVE, colors=255
    )
    palette = paletted.getpalette() or []
    palette = (palette + [0] * 768)[:768]
    palette[255 * 3 : 255 * 3 + 3] = [0, 0, 0]
    paletted.putpalette(palette)
    transparent_mask = alpha.point(lambda a: 255 if a < 128 else 0)
    paletted.paste(255, mask=transparent_mask)
    paletted.info["transparency"] = 255
    return paletted


def save_gif(
    frames: list[Image.Image],
    path: str | Path,
    *,
    durations: list[int],
    loop: int = 0,
) -> None:
    """frames 是同尺寸的 RGBA 帧（load_gif_frames 读出的帧已合成到整幅画布）"""
    if _has_transparency(frames):
        gif_frames = [_rgba_to_transparent_p(frame) for frame in frames]
        options = {"disposal": 2, "transparency": 255, "optimize": False}
    else:
        gif_frames = [frame.convert("RGB") for frame in frames]
        options = {"optimize": True}
    gif_frames[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=gif_frames[1:],
        duration=durations,
        loop=loop,
        **options,
    )


def retime_frames_for_speed(
    frames: list[Image.Image],
    durations: list[int],
    speed_factor: float,
    *,
    min_duration_ms: int = GIF_MIN_DURATION_MS,
) -> tuple[list[Image.Image], list[int]]:
    # GIF 帧时长以 10ms 为单位，Pillow 写入时直接截断，逐帧取整会累积误差：
    # 先算每帧的结束时间点再对齐到 10ms 网格，不足最短时长的帧丢掉，由下一帧补上
    min_cs = math.ceil(min_duration_ms / 10)
    new_frames: list[Image.Image] = []
    new_durations: list[int] = []
    elapsed = 0
    start_cs = 0
    for frame, duration in zip(frames, durations):
        elapsed += duration
        end_cs = round(elapsed / speed_factor / 10)
        if end_cs - start_cs < min_cs:
            continue
        new_frames.append(frame)
        new_durations.append((end_cs - start_cs) * 10)
        start_cs = end_cs

    total_cs = round(elapsed / speed_factor / 10)
    if not new_frames:
        return [frames[0]], [max(min_cs, total_cs) * 10]
    new_durations[-1] += (total_cs - start_cs) * 10
    return new_frames, new_durations
