import asyncio
import os
import tempfile
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageSequence
from nonebot.log import logger

from ..utils.network import get_client_session, get_effective_proxy

IMAGE_PROCESSOR_MAX_IMAGE_BYTES = int(os.getenv("HAKUBOT_IMAGE_PROCESSOR_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
IMAGE_PROCESSOR_MAX_GIF_BYTES = int(os.getenv("HAKUBOT_IMAGE_PROCESSOR_MAX_GIF_BYTES", str(50 * 1024 * 1024)))
IMAGE_PROCESSOR_MAX_VIDEO_BYTES = int(os.getenv("HAKUBOT_IMAGE_PROCESSOR_MAX_VIDEO_BYTES", str(200 * 1024 * 1024)))
IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT = float(os.getenv("HAKUBOT_IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT", "60"))
IMAGE_PROCESSOR_VIDEO_DOWNLOAD_TIMEOUT = float(os.getenv("HAKUBOT_IMAGE_PROCESSOR_VIDEO_DOWNLOAD_TIMEOUT", "180"))
GIF_MIN_DURATION_MS = int(os.getenv("HAKUBOT_IMAGE_PROCESSOR_GIF_MIN_DURATION_MS", "20"))


async def safe_delete_file(file_path: str | Path | None, max_retries: int = 3) -> bool:
    if not file_path:
        return True

    path = Path(file_path)
    for i in range(max_retries):
        try:
            if path.exists():
                path.unlink()
            return True
        except PermissionError as e:
            if i < max_retries - 1:
                await asyncio.sleep(0.1)
            else:
                logger.warning(f"删除文件失败: {path}: {e}")
                return False
        except Exception as e:
            logger.warning(f"删除文件失败: {path}: {e}")
            return False
    return False


async def cleanup_files(*paths: str | Path | None) -> None:
    for path in paths:
        await safe_delete_file(path)


def ensure_output_dir(name: str) -> Path:
    output_dir = Path(tempfile.gettempdir()) / name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def guess_ext_from_url(url: str, default: str = "tmp") -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix and len(suffix) <= 8:
        return suffix
    return default.lstrip(".") or "tmp"


async def download_to_temp(
    url: str,
    *,
    ext: str = "tmp",
    prefix: str = "download",
    max_bytes: int = IMAGE_PROCESSOR_MAX_IMAGE_BYTES,
    timeout_total: float = IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    allowed_statuses: Iterable[int] = (200, 206),
) -> str:
    suffix = ext.lstrip(".") or guess_ext_from_url(url)
    fd, temp_path = tempfile.mkstemp(prefix=f"{prefix}_{os.urandom(4).hex()}_", suffix=f".{suffix}")
    os.close(fd)
    path = Path(temp_path)
    timeout = aiohttp.ClientTimeout(total=timeout_total, connect=min(30, timeout_total), sock_read=timeout_total)
    proxy = get_effective_proxy(proxy)

    try:
        async with get_client_session().get(
            url,
            headers=headers,
            proxy=proxy,
            timeout=timeout,
            verify_ssl=False,
        ) as response:
            if response.status not in set(allowed_statuses):
                raise Exception(f"下载失败: HTTP {response.status}")

            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise Exception(f"文件过大: {int(content_length)} bytes > {max_bytes} bytes")

            downloaded = 0
            with open(path, "wb") as f:
                async for chunk in response.content.iter_chunked(1024 * 512):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise Exception(f"文件过大: 已下载 {downloaded} bytes > {max_bytes} bytes")
                    f.write(chunk)

        return str(path)
    except Exception:
        await safe_delete_file(path)
        raise


def _normalize_durations(durations: list[int], frame_count: int) -> list[int]:
    if not durations:
        return [100] * frame_count
    normalized = [max(1, int(d or 100)) for d in durations]
    if len(normalized) < frame_count:
        normalized.extend([normalized[-1]] * (frame_count - len(normalized)))
    return normalized[:frame_count]


def load_gif_frames(image_path: str | Path) -> tuple[list[Image.Image], list[int], dict]:
    frames: list[Image.Image] = []
    durations: list[int] = []
    meta: dict = {}

    with Image.open(image_path) as img:
        meta["loop"] = int(img.info.get("loop", 0) or 0)
        default_duration = int(img.info.get("duration", 100) or 100)
        for frame in ImageSequence.Iterator(img):
            durations.append(int(frame.info.get("duration", default_duration) or default_duration))
            frames.append(frame.convert("RGBA").copy())

    return frames, _normalize_durations(durations, len(frames)), meta


def _has_transparency(frames: list[Image.Image]) -> bool:
    for frame in frames:
        if frame.mode == "RGBA" and frame.getextrema()[3][0] < 255:
            return True
        if frame.mode in ("LA", "P") and "transparency" in frame.info:
            return True
    return False


def _rgba_to_transparent_p(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = Image.new("RGB", rgba.size, (0, 0, 0))
    rgb.paste(rgba.convert("RGB"), mask=alpha)
    paletted = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
    palette = paletted.getpalette() or []
    palette = (palette + [0] * 768)[:768]
    palette[255 * 3 : 255 * 3 + 3] = [0, 0, 0]
    paletted.putpalette(palette)
    transparent_mask = alpha.point(lambda a: 255 if a <= 0 else 0)
    paletted.paste(255, mask=transparent_mask)
    paletted.info["transparency"] = 255
    return paletted


def _normalize_frame_sizes(frames: list[Image.Image]) -> list[Image.Image]:
    if not frames:
        return []
    size = frames[0].size
    normalized = []
    for frame in frames:
        current = frame.convert("RGBA") if frame.mode != "RGBA" else frame.copy()
        if current.size != size:
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(current, (0, 0), current if current.mode == "RGBA" else None)
            current = canvas
        normalized.append(current)
    return normalized


def save_gif(
    frames: list[Image.Image],
    output_path: str | Path,
    *,
    durations: list[int] | int,
    loop: int = 0,
    preserve_transparency: bool = True,
    optimize_rgb: bool = True,
) -> bool:
    if not frames:
        return False

    normalized_frames = _normalize_frame_sizes(frames)
    if isinstance(durations, int):
        normalized_durations = [max(1, int(durations))] * len(normalized_frames)
    else:
        normalized_durations = _normalize_durations(durations, len(normalized_frames))

    transparent = preserve_transparency and _has_transparency(normalized_frames)
    if transparent:
        gif_frames = [_rgba_to_transparent_p(frame) for frame in normalized_frames]
        save_kwargs = {
            "save_all": True,
            "append_images": gif_frames[1:],
            "duration": normalized_durations,
            "loop": loop,
            "disposal": 2,
            "transparency": 255,
            "optimize": False,
            "format": "GIF",
        }
    else:
        gif_frames = [frame.convert("RGB") for frame in normalized_frames]
        save_kwargs = {
            "save_all": True,
            "append_images": gif_frames[1:],
            "duration": normalized_durations,
            "loop": loop,
            "optimize": optimize_rgb,
            "format": "GIF",
        }

    gif_frames[0].save(str(output_path), **save_kwargs)
    return True


def _round_durations(values: list[float], target_total: float, min_duration_ms: int) -> list[int]:
    rounded = [max(min_duration_ms, int(round(v))) for v in values]
    diff = int(round(target_total)) - sum(rounded)

    if diff > 0:
        i = 0
        while diff > 0 and rounded:
            rounded[i % len(rounded)] += 1
            diff -= 1
            i += 1
    elif diff < 0:
        i = 0
        attempts = 0
        while diff < 0 and rounded and attempts < len(rounded) * max(1, abs(diff) + 1):
            idx = i % len(rounded)
            if rounded[idx] > min_duration_ms:
                rounded[idx] -= 1
                diff += 1
            i += 1
            attempts += 1

    return rounded


def retime_frames_for_speed(
    frames: list[Image.Image],
    durations: list[int],
    speed_factor: float,
    *,
    min_duration_ms: int = GIF_MIN_DURATION_MS,
) -> tuple[list[Image.Image], list[int]]:
    if not frames:
        return [], []

    speed = max(0.01, float(speed_factor))
    source_durations = _normalize_durations(durations, len(frames))
    raw_durations = [d / speed for d in source_durations]

    if min(raw_durations) >= min_duration_ms:
        return [frame.copy() for frame in frames], _round_durations(raw_durations, sum(raw_durations), min_duration_ms)

    original_total = sum(source_durations)
    target_total = max(float(min_duration_ms), original_total / speed)
    target_frame_count = max(1, int(round(target_total / min_duration_ms)))
    target_frame_count = min(target_frame_count, len(frames))

    cumulative: list[int] = []
    total = 0
    for duration in source_durations:
        total += duration
        cumulative.append(total)

    sampled_frames: list[Image.Image] = []
    for i in range(target_frame_count):
        source_time = (i * target_total / target_frame_count) * speed
        source_time = min(source_time, original_total - 1)
        source_index = 0
        while source_index < len(cumulative) - 1 and source_time >= cumulative[source_index]:
            source_index += 1
        sampled_frames.append(frames[source_index].copy())

    base_duration = target_total / target_frame_count
    sampled_durations = _round_durations([base_duration] * target_frame_count, target_total, min_duration_ms)
    return sampled_frames, sampled_durations


def fix_frame_for_gif(im: Image.Image) -> Image.Image:
    if im.mode == "RGBA" and im.getextrema()[3][0] < 255:
        return _rgba_to_transparent_p(im)
    if im.mode == "RGBA":
        return im.convert("RGB")
    if im.mode == "P" and getattr(im.palette, "mode", None) == "RGBA":
        rgb_palette = im.getpalette()
        im.putpalette(rgb_palette)
    return im
