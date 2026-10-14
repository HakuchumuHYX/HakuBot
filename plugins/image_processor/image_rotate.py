# image_rotate.py
import math
import os

from nonebot import logger
from PIL import Image

from utils.concurrency import run_in_pool
from plugins.image_processor.utils import (
    download_image,
    load_gif_frames,
    open_static_image,
    save_gif,
)
from utils.files import ensure_output_dir

# 转圈图不需要原图分辨率，大图逐帧旋转又慢又大（1280² 原来要 30 秒、19MB）
ROTATE_MAX_SIDE = 400


def make_square_canvas(img: Image.Image) -> Image.Image:
    img = img.copy()
    img.thumbnail((ROTATE_MAX_SIDE, ROTATE_MAX_SIDE), Image.Resampling.LANCZOS)
    width, height = img.size
    diagonal = int(math.sqrt(width**2 + height**2)) + 2
    canvas = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    offset_x = (diagonal - width) // 2
    offset_y = (diagonal - height) // 2
    rgba = img.convert("RGBA")
    canvas.paste(rgba, (offset_x, offset_y), rgba)
    return canvas


def _process_rotate_file(
    image_path: str, animated: bool, direction: str, speed: float
) -> str:
    frames_per_circle = max(4, min(200, int(40 / speed)))
    step_angle = 360.0 / frames_per_circle
    if direction == "clockwise":
        step_angle = -step_angle

    logger.debug(
        f"旋转参数: 倍速{speed}, 总帧数{frames_per_circle}, 步进{step_angle:.2f}度"
    )

    if animated:
        original_frames, _, _ = load_gif_frames(image_path)
    else:
        original_frames = [open_static_image(image_path)]

    # 预乘 alpha 后再插值，否则透明角落的黑色会混进边缘
    canvases = [make_square_canvas(frame).convert("RGBa") for frame in original_frames]
    output_frames = [
        canvases[i % len(canvases)]
        .rotate(i * step_angle, resample=Image.Resampling.BICUBIC)
        .convert("RGBA")
        for i in range(frames_per_circle)
    ]

    output_dir = ensure_output_dir("nonebot_image_rotate")
    output_path = output_dir / f"rotate_{direction}_{speed}x_{os.urandom(4).hex()}.gif"
    save_gif(
        output_frames,
        output_path,
        durations=[50] * len(output_frames),
        loop=0,
        optimize_rgb=False,
    )
    return str(output_path)


async def process_image_rotate(image_url: str, direction: str, speed: float) -> str:
    async with download_image(image_url) as (image_path, animated):
        return await run_in_pool(
            _process_rotate_file, image_path, animated, direction, speed
        )
