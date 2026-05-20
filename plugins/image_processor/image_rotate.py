# image_rotate.py
import math
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


def make_square_canvas(img: Image.Image) -> Image.Image:
    width, height = img.size
    diagonal = int(math.sqrt(width**2 + height**2)) + 2
    canvas = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    offset_x = (diagonal - width) // 2
    offset_y = (diagonal - height) // 2
    rgba = img.convert("RGBA")
    canvas.paste(rgba, (offset_x, offset_y), rgba)
    return canvas


def _process_rotate_file(image_path: str, direction: str, speed: float) -> str:
    speed = max(0.1, min(float(speed), 5.0))
    frames_per_circle = max(4, min(200, int(40 / speed)))
    step_angle = 360.0 / frames_per_circle
    if direction == "clockwise":
        step_angle = -step_angle

    logger.info(f"旋转参数: 倍速{speed}, 总帧数{frames_per_circle}, 步进{step_angle:.2f}度")

    with Image.open(image_path) as input_img:
        if bool(getattr(input_img, "is_animated", False)):
            original_frames, _, _ = load_gif_frames(image_path)
        else:
            original_frames = [input_img.convert("RGBA").copy()]

    if not original_frames:
        raise Exception("没有可处理的图片帧")

    base_frame = original_frames[0]
    w, h = base_frame.size
    diagonal = int(math.sqrt(w**2 + h**2)) + 2
    canvas_size = (diagonal, diagonal)
    output_frames = []

    for i in range(frames_per_circle):
        current_angle = i * step_angle
        source_frame = original_frames[i % len(original_frames)].convert("RGBA")
        frame_canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        offset_x = (diagonal - source_frame.size[0]) // 2
        offset_y = (diagonal - source_frame.size[1]) // 2
        frame_canvas.paste(source_frame, (offset_x, offset_y), source_frame)
        output_frames.append(frame_canvas.rotate(current_angle, resample=Image.Resampling.BICUBIC))

    output_dir = ensure_output_dir("nonebot_image_rotate")
    output_path = output_dir / f"rotate_{direction}_{speed}x_{os.urandom(4).hex()}.gif"
    save_gif(output_frames, output_path, durations=[50] * len(output_frames), loop=0, optimize_rgb=False)
    return str(output_path)


async def process_rotate(image_path: str, direction: str, speed: float) -> str:
    try:
        return await run_in_pool(_process_rotate_file, image_path, direction, speed)
    except Exception as e:
        logger.error(f"旋转处理错误: {e}")
        return ""


async def process_image_rotate(image_url: str, direction: str, speed: float) -> str:
    image_path = ""
    try:
        ext = guess_ext_from_url(image_url, "jpg")
        max_bytes = IMAGE_PROCESSOR_MAX_GIF_BYTES if ext == "gif" else IMAGE_PROCESSOR_MAX_IMAGE_BYTES
        image_path = await download_to_temp(
            image_url,
            ext=ext,
            prefix="temp_img",
            max_bytes=max_bytes,
            timeout_total=IMAGE_PROCESSOR_IMAGE_DOWNLOAD_TIMEOUT,
        )
        result_path = await process_rotate(image_path, direction, speed)
        if result_path and os.path.exists(result_path):
            logger.info(f"旋转成功: {result_path}, 大小: {os.path.getsize(result_path)}")
            return result_path
        return ""
    except Exception as e:
        logger.error(f"主旋转函数出错: {e}")
        return ""
    finally:
        await safe_delete_file(image_path)
