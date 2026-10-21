# image_symmetry.py
import asyncio
import os

from PIL import Image
from nonebot import logger

from plugins.image_processor.utils import (
    convert_to_supported_mode,
    download_image,
    load_gif_frames,
    open_static_image,
    save_gif,
)
from utils.files import ensure_output_dir


def _left_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_w = (width + 1) // 2
    mirror_w = width - keep_w
    result = Image.new(img.mode, (width, height))
    left = img.crop((0, 0, keep_w, height))
    result.paste(left, (0, 0))
    if mirror_w > 0:
        mirrored = left.transpose(Image.FLIP_LEFT_RIGHT).crop(
            (keep_w - mirror_w, 0, keep_w, height)
        )
        result.paste(mirrored, (keep_w, 0))
    return result


def _right_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_x = width // 2
    mirror_w = keep_x
    result = Image.new(img.mode, (width, height))
    right = img.crop((keep_x, 0, width, height))
    result.paste(right, (keep_x, 0))
    if mirror_w > 0:
        mirrored = right.transpose(Image.FLIP_LEFT_RIGHT).crop((0, 0, mirror_w, height))
        result.paste(mirrored, (0, 0))
    return result


def _top_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_h = (height + 1) // 2
    mirror_h = height - keep_h
    result = Image.new(img.mode, (width, height))
    top = img.crop((0, 0, width, keep_h))
    result.paste(top, (0, 0))
    if mirror_h > 0:
        mirrored = top.transpose(Image.FLIP_TOP_BOTTOM).crop(
            (0, keep_h - mirror_h, width, keep_h)
        )
        result.paste(mirrored, (0, keep_h))
    return result


def _bottom_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_y = height // 2
    mirror_h = keep_y
    result = Image.new(img.mode, (width, height))
    bottom = img.crop((0, keep_y, width, height))
    result.paste(bottom, (0, keep_y))
    if mirror_h > 0:
        mirrored = bottom.transpose(Image.FLIP_TOP_BOTTOM).crop((0, 0, width, mirror_h))
        result.paste(mirrored, (0, 0))
    return result


def _center_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_w = (width + 1) // 2
    keep_h = (height + 1) // 2
    mirror_w = width - keep_w
    mirror_h = height - keep_h

    result = Image.new(img.mode, (width, height))
    top_left = img.crop((0, 0, keep_w, keep_h))
    result.paste(top_left, (0, 0))

    if mirror_w > 0:
        top_right = top_left.transpose(Image.FLIP_LEFT_RIGHT).crop(
            (keep_w - mirror_w, 0, keep_w, keep_h)
        )
        result.paste(top_right, (keep_w, 0))

    if mirror_h > 0:
        top_band = result.crop((0, 0, width, keep_h))
        bottom_band = top_band.transpose(Image.FLIP_TOP_BOTTOM).crop(
            (0, keep_h - mirror_h, width, keep_h)
        )
        result.paste(bottom_band, (0, keep_h))

    return result


def _symmetry_image(img: Image.Image, symmetry_type: str) -> Image.Image:
    if symmetry_type == "right":
        return _right_symmetry_image(img)
    if symmetry_type == "center":
        return _center_symmetry_image(img)
    if symmetry_type == "top":
        return _top_symmetry_image(img)
    if symmetry_type == "bottom":
        return _bottom_symmetry_image(img)
    return _left_symmetry_image(img)


def _process_gif_symmetry_file(image_path: str, symmetry_type: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功处理的帧")

    processed_frames = [_symmetry_image(frame, symmetry_type) for frame in frames]
    output_dir = ensure_output_dir("nonebot_image_symmetry")
    output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.gif"
    save_gif(
        processed_frames,
        output_path,
        durations=durations,
        loop=int(meta.get("loop", 0)),
    )
    logger.debug(f"GIF对称处理完成: {len(processed_frames)}帧, 保存到 {output_path}")
    return str(output_path)


def _process_static_symmetry_file(image_path: str, symmetry_type: str) -> str:
    processed_image = _symmetry_image(open_static_image(image_path), symmetry_type)
    output_dir = ensure_output_dir("nonebot_image_symmetry")
    output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.png"
    processed_image.save(output_path, "PNG")
    return str(output_path)


async def process_image_symmetry(image_url: str, symmetry_type: str) -> str:
    async with download_image(image_url) as (image_path, animated):
        process = (
            _process_gif_symmetry_file if animated else _process_static_symmetry_file
        )
        return await asyncio.to_thread(process, image_path, symmetry_type)
