# image_mirror.py
import os

from nonebot.log import logger
from PIL import Image

from ..utils.tools import run_in_pool
from .utils import (
    IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
    IMAGE_PROCESSOR_MAX_GIF_BYTES,
    IMAGE_PROCESSOR_MAX_IMAGE_BYTES,
    download_to_temp,
    ensure_output_dir,
    guess_ext_from_url,
    load_gif_frames,
    safe_delete_file,
    save_gif,
)


def convert_to_supported_mode(img: Image.Image) -> Image.Image:
    if img.mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode == "LA":
        return img.convert("RGBA")
    if img.mode not in ["RGB", "RGBA"]:
        return img.convert("RGB")
    return img.copy()


def get_transpose_method(direction: str):
    if direction in ["top", "bottom", "vertical"]:
        return Image.FLIP_TOP_BOTTOM
    return Image.FLIP_LEFT_RIGHT


def _process_static_mirror_file(image_path: str, direction: str) -> str:
    with Image.open(image_path) as img:
        processed_image = convert_to_supported_mode(img).transpose(get_transpose_method(direction))
        output_dir = ensure_output_dir("nonebot_image_mirror")
        output_path = output_dir / f"mirror_{direction}_{os.urandom(4).hex()}.png"
        processed_image.save(output_path, "PNG")
        return str(output_path)


def _process_gif_mirror_file(image_path: str, direction: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功处理的帧")

    method = get_transpose_method(direction)
    processed_frames = [frame.transpose(method) for frame in frames]
    output_dir = ensure_output_dir("nonebot_image_mirror")
    output_path = output_dir / f"mirror_{direction}_{os.urandom(4).hex()}.gif"
    save_gif(processed_frames, output_path, durations=durations, loop=int(meta.get("loop", 0)))
    return str(output_path)


async def process_static_mirror(image_path: str, direction: str) -> str:
    try:
        return await run_in_pool(_process_static_mirror_file, image_path, direction)
    except Exception as e:
        logger.error(f"静态图片镜像处理错误: {e}")
        return ""


async def process_gif_mirror(image_path: str, direction: str) -> str:
    try:
        return await run_in_pool(_process_gif_mirror_file, image_path, direction)
    except Exception as e:
        logger.error(f"GIF镜像处理错误: {e}")
        return ""


async def process_image_mirror(image_url: str, direction: str) -> str:
    image_path = ""
    try:
        logger.info(f"开始处理镜像图片: {direction}...")
        ext = guess_ext_from_url(image_url, "jpg")
        max_bytes = IMAGE_PROCESSOR_MAX_GIF_BYTES if ext == "gif" else IMAGE_PROCESSOR_MAX_IMAGE_BYTES
        image_path = await download_to_temp(
            image_url,
            ext=ext,
            prefix="temp_img",
            max_bytes=max_bytes,
            timeout_total=IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
        )

        is_gif = False
        try:
            with Image.open(image_path) as img:
                is_gif = bool(getattr(img, "is_animated", False))
        except Exception:
            is_gif = False

        result_path = await process_gif_mirror(image_path, direction) if is_gif else await process_static_mirror(image_path, direction)
        return result_path if result_path and os.path.exists(result_path) else ""
    except Exception as e:
        logger.error(f"镜像处理出错: {e}")
        return ""
    finally:
        await safe_delete_file(image_path)
