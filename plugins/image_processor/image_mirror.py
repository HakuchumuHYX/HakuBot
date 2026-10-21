# image_mirror.py
import asyncio
import os

from PIL import Image

from plugins.image_processor.utils import (
    convert_to_supported_mode,
    download_image,
    load_gif_frames,
    open_static_image,
    save_gif,
)
from utils.files import ensure_output_dir


def get_transpose_method(direction: str):
    if direction in ["top", "bottom", "vertical"]:
        return Image.FLIP_TOP_BOTTOM
    return Image.FLIP_LEFT_RIGHT


def _process_static_mirror_file(image_path: str, direction: str) -> str:
    processed_image = convert_to_supported_mode(
        open_static_image(image_path)
    ).transpose(get_transpose_method(direction))
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
    save_gif(
        processed_frames,
        output_path,
        durations=durations,
        loop=int(meta.get("loop", 0)),
    )
    return str(output_path)


async def process_image_mirror(image_url: str, direction: str) -> str:
    async with download_image(image_url) as (image_path, animated):
        process = _process_gif_mirror_file if animated else _process_static_mirror_file
        return await asyncio.to_thread(process, image_path, direction)
