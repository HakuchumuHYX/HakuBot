# image_rotate.py
import bisect
import itertools
import math

from nonebot import logger
from PIL import Image

from plugins.image_processor.utils import (
    download_image,
    load_gif_frames,
    open_static_image,
    output_path,
    run_heavy,
    save_gif,
)

# 转圈图不需要原图分辨率，大图逐帧旋转又慢又大（1280² 原来要 30 秒、19MB）
ROTATE_MAX_SIDE = 400
ROTATE_FRAME_MS = 50
# 每圈帧数和总帧数共用的上限：200 帧 568² 的 RGBA 约 260MB
ROTATE_MAX_FRAMES = 200


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
    frames_per_circle = max(4, min(ROTATE_MAX_FRAMES, int(40 / speed)))
    step_angle = 360.0 / frames_per_circle
    if direction == "clockwise":
        step_angle = -step_angle
    circle_ms = frames_per_circle * ROTATE_FRAME_MS

    if animated:
        source_frames, durations, _ = load_gif_frames(image_path)
    else:
        # 静态图当成一帧、正好转一圈的动图
        source_frames, durations = [open_static_image(image_path)], [circle_ms]

    # 动图按时间线取帧：输出第 i 帧显示原图在 i×50ms 时刻的那一帧，
    # 原图放完一遍要转几圈就转几圈（整圈，首尾才接得上），总帧数封顶
    total_ms = sum(durations)
    circles = max(
        1,
        min(math.ceil(total_ms / circle_ms), ROTATE_MAX_FRAMES // frames_per_circle),
    )
    frame_ends = list(itertools.accumulate(durations))

    logger.debug(
        f"旋转参数: 倍速{speed}, 每圈{frames_per_circle}帧, {circles}圈, "
        f"步进{step_angle:.2f}度, 原图{len(source_frames)}帧/{total_ms}ms"
    )

    # 预乘 alpha 后再插值，否则透明角落的黑色会混进边缘；只给用到的源帧做画布
    canvases: dict[int, Image.Image] = {}
    output_frames = []
    for i in range(circles * frames_per_circle):
        index = bisect.bisect_right(frame_ends, (i * ROTATE_FRAME_MS) % total_ms)
        if index not in canvases:
            canvases[index] = make_square_canvas(source_frames[index]).convert("RGBa")
        output_frames.append(
            canvases[index]
            .rotate(i * step_angle, resample=Image.Resampling.BICUBIC)
            .convert("RGBA")
        )

    path = output_path(f"rotate_{direction}", "gif")
    save_gif(
        output_frames,
        path,
        durations=[ROTATE_FRAME_MS] * len(output_frames),
        loop=0,
    )
    return str(path)


async def process_image_rotate(image_url: str, direction: str, speed: float) -> str:
    async with download_image(image_url) as (image_path, animated):
        return await run_heavy(
            _process_rotate_file, image_path, animated, direction, speed
        )
