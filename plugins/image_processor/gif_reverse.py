# gif_reverse.py
import os

from nonebot.log import logger
from PIL import Image

from ..utils.tools import run_in_pool
from .utils import (
    IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
    IMAGE_PROCESSOR_MAX_GIF_BYTES,
    download_to_temp,
    ensure_output_dir,
    load_gif_frames,
    safe_delete_file,
    save_gif,
)


def _reverse_gif_file(image_path: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功提取到任何帧")

    reversed_frames = list(reversed(frames))
    reversed_durations = list(reversed(durations))

    output_dir = ensure_output_dir("nonebot_gif_reverse")
    output_path = output_dir / f"reversed_{os.urandom(4).hex()}.gif"

    if not save_gif(reversed_frames, output_path, durations=reversed_durations, loop=int(meta.get("loop", 0))):
        raise Exception("GIF保存失败")

    logger.info(f"GIF倒放成功: {len(frames)}帧")
    return str(output_path)


async def reverse_gif(image_url: str) -> str:
    temp_path = ""
    try:
        temp_path = await download_to_temp(
            image_url,
            ext="gif",
            prefix="temp_gif",
            max_bytes=IMAGE_PROCESSOR_MAX_GIF_BYTES,
            timeout_total=IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
        )
        return await run_in_pool(_reverse_gif_file, temp_path)
    except Exception as e:
        logger.error(f"GIF倒放错误: {e}")
        return ""
    finally:
        await safe_delete_file(temp_path)


def reverse_gif_alternative(image_url: str) -> str:
    try:
        import requests
        from io import BytesIO

        response = requests.get(image_url, timeout=(10, 60))
        response.raise_for_status()
        if len(response.content) > IMAGE_PROCESSOR_MAX_GIF_BYTES:
            raise Exception("GIF文件过大")

        with Image.open(BytesIO(response.content)) as img:
            frames = []
            durations = []
            default_duration = int(img.info.get("duration", 100) or 100)
            loop = int(img.info.get("loop", 0) or 0)
            for index in range(int(getattr(img, "n_frames", 1))):
                img.seek(index)
                frames.append(img.convert("RGBA").copy())
                durations.append(int(img.info.get("duration", default_duration) or default_duration))

        if not frames:
            raise Exception("没有成功提取到任何帧")

        output_dir = ensure_output_dir("nonebot_gif_reverse")
        output_path = output_dir / f"reversed_alt_{os.urandom(4).hex()}.gif"
        save_gif(list(reversed(frames)), output_path, durations=list(reversed(durations)), loop=loop)
        return str(output_path)
    except Exception as e:
        logger.error(f"备选方案GIF倒放错误: {e}")
        return ""
