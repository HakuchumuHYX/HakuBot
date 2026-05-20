# image_symmetry.py
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


def _left_symmetry_image(img: Image.Image) -> Image.Image:
    img = convert_to_supported_mode(img)
    width, height = img.size
    keep_w = (width + 1) // 2
    mirror_w = width - keep_w
    result = Image.new(img.mode, (width, height))
    left = img.crop((0, 0, keep_w, height))
    result.paste(left, (0, 0))
    if mirror_w > 0:
        mirrored = left.transpose(Image.FLIP_LEFT_RIGHT).crop((keep_w - mirror_w, 0, keep_w, height))
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
        mirrored = top.transpose(Image.FLIP_TOP_BOTTOM).crop((0, keep_h - mirror_h, width, keep_h))
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
        top_right = top_left.transpose(Image.FLIP_LEFT_RIGHT).crop((keep_w - mirror_w, 0, keep_w, keep_h))
        result.paste(top_right, (keep_w, 0))

    if mirror_h > 0:
        top_band = result.crop((0, 0, width, keep_h))
        bottom_band = top_band.transpose(Image.FLIP_TOP_BOTTOM).crop((0, keep_h - mirror_h, width, keep_h))
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


def _process_symmetry_file(image_path: str, symmetry_type: str) -> Image.Image:
    with Image.open(image_path) as img:
        return _symmetry_image(img, symmetry_type)


async def process_left_symmetry(image_path: str) -> Image.Image | None:
    try:
        return await run_in_pool(_process_symmetry_file, image_path, "left")
    except Exception as e:
        logger.error(f"左对称处理错误: {e}")
        return None


async def process_right_symmetry(image_path: str) -> Image.Image | None:
    try:
        return await run_in_pool(_process_symmetry_file, image_path, "right")
    except Exception as e:
        logger.error(f"右对称处理错误: {e}")
        return None


async def process_center_symmetry(image_path: str) -> Image.Image | None:
    try:
        return await run_in_pool(_process_symmetry_file, image_path, "center")
    except Exception as e:
        logger.error(f"中心对称处理错误: {e}")
        return None


async def process_top_symmetry(image_path: str) -> Image.Image | None:
    try:
        return await run_in_pool(_process_symmetry_file, image_path, "top")
    except Exception as e:
        logger.error(f"上对称处理错误: {e}")
        return None


async def process_bottom_symmetry(image_path: str) -> Image.Image | None:
    try:
        return await run_in_pool(_process_symmetry_file, image_path, "bottom")
    except Exception as e:
        logger.error(f"下对称处理错误: {e}")
        return None


def _process_gif_symmetry_file(image_path: str, symmetry_type: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功处理的帧")

    processed_frames = [_symmetry_image(frame, symmetry_type) for frame in frames]
    output_dir = ensure_output_dir("nonebot_image_symmetry")
    output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.gif"
    save_gif(processed_frames, output_path, durations=durations, loop=int(meta.get("loop", 0)))
    logger.info(f"GIF对称处理完成: {len(processed_frames)}帧, 保存到 {output_path}")
    return str(output_path)


async def process_gif_symmetry(image_path: str, symmetry_type: str) -> str:
    try:
        return await run_in_pool(_process_gif_symmetry_file, image_path, symmetry_type)
    except Exception as e:
        logger.error(f"GIF对称处理错误: {e}")
        return ""


def _process_static_symmetry_file(image_path: str, symmetry_type: str) -> str:
    processed_image = _process_symmetry_file(image_path, symmetry_type)
    output_dir = ensure_output_dir("nonebot_image_symmetry")
    output_path = output_dir / f"symmetry_{symmetry_type}_{os.urandom(4).hex()}.png"
    processed_image.save(output_path, "PNG")
    return str(output_path)


async def process_image_symmetry(image_url: str, symmetry_type: str) -> str:
    image_path = ""
    try:
        logger.info(f"开始处理对称图片: {symmetry_type}, URL: {image_url[:100]}...")
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
        except Exception as img_error:
            logger.error(f"检查图片格式时出错: {img_error}")

        if is_gif:
            result_path = await process_gif_symmetry(image_path, symmetry_type)
        else:
            result_path = await run_in_pool(_process_static_symmetry_file, image_path, symmetry_type)

        if result_path and os.path.exists(result_path):
            logger.info(f"对称处理成功: {result_path}, 大小: {os.path.getsize(result_path)}")
            return result_path
        logger.error("对称处理失败: 未生成有效输出文件")
        return ""
    except Exception as e:
        logger.error(f"对称处理过程出错: {e}")
        return ""
    finally:
        await safe_delete_file(image_path)
