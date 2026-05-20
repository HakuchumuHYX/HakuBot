# gif_speed.py
import os
from pathlib import Path

from nonebot.log import logger
from PIL import Image

from ..utils.tools import run_in_pool
from .utils import (
    IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
    IMAGE_PROCESSOR_MAX_GIF_BYTES,
    download_to_temp,
    ensure_output_dir,
    load_gif_frames,
    retime_frames_for_speed,
    safe_delete_file,
    save_gif,
)


def _change_gif_speed_file(image_path: str, speed_factor: float) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功提取到任何帧")

    new_frames, new_durations = retime_frames_for_speed(frames, durations, speed_factor)

    output_dir = ensure_output_dir("nonebot_gif_speed")
    output_path = output_dir / f"speed_{speed_factor}x_{os.urandom(4).hex()}.gif"

    if not save_gif(new_frames, output_path, durations=new_durations, loop=int(meta.get("loop", 0))):
        raise Exception("GIF保存失败")

    logger.info(
        f"GIF倍速处理成功: {len(frames)}帧 -> {len(new_frames)}帧, "
        f"原时长={sum(durations)}ms, 新时长={sum(new_durations)}ms, 倍速={speed_factor}"
    )
    return str(output_path)


async def change_gif_speed(image_url: str, speed_factor: float) -> str:
    temp_path = ""
    try:
        speed_factor = min(float(speed_factor), 5.0)
        temp_path = await download_to_temp(
            image_url,
            ext="gif",
            prefix="temp_gif",
            max_bytes=IMAGE_PROCESSOR_MAX_GIF_BYTES,
            timeout_total=IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
        )
        return await run_in_pool(_change_gif_speed_file, temp_path, speed_factor)
    except Exception as e:
        logger.error(f"GIF倍速处理错误: {e}")
        return ""
    finally:
        await safe_delete_file(temp_path)


def change_gif_speed_alternative(image_url: str, speed_factor: float) -> str:
    try:
        import requests
        from io import BytesIO

        speed_factor = min(float(speed_factor), 5.0)
        response = requests.get(image_url, timeout=(10, 60))
        response.raise_for_status()
        if len(response.content) > IMAGE_PROCESSOR_MAX_GIF_BYTES:
            raise Exception("GIF文件过大")

        with Image.open(BytesIO(response.content)) as img:
            frames = []
            durations = []
            default_duration = int(img.info.get("duration", 100) or 100)
            loop = int(img.info.get("loop", 0) or 0)
            for frame in getattr(img, "n_frames", []) and range(img.n_frames) or []:
                img.seek(frame)
                frames.append(img.convert("RGBA").copy())
                durations.append(int(img.info.get("duration", default_duration) or default_duration))

        if not frames:
            raise Exception("没有成功提取到任何帧")

        new_frames, new_durations = retime_frames_for_speed(frames, durations, speed_factor)
        output_dir = ensure_output_dir("nonebot_gif_speed")
        output_path = output_dir / f"speed_alt_{speed_factor}x_{os.urandom(4).hex()}.gif"
        save_gif(new_frames, output_path, durations=new_durations, loop=loop)
        return str(output_path)
    except Exception as e:
        logger.error(f"备选方案GIF倍速错误: {e}")
        return ""
