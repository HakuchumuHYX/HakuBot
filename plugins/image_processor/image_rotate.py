# image_rotate.py
import math

from nonebot import logger
from PIL import Image

from plugins.image_processor.utils import (
    GIF_MIN_DURATION_MS,
    download_image,
    load_gif_frames,
    open_static_image,
    output_path,
    retime_frames_for_speed,
    run_heavy,
    save_gif,
)

# 转圈图不需要原图分辨率，大图逐帧旋转又慢又大（1280² 原来要 30 秒、19MB）
ROTATE_MAX_SIDE = 400
# 超预算时先缩边长，缩到这里还超才降帧
ROTATE_MIN_SIDE = 200
# 1 倍速转一圈的时长；长帧按约 50ms 的步长切段补旋转角度
ROTATE_CIRCLE_MS = 2000
ROTATE_STEP_MS = 50
# 输出帧数 × 画布面积的上限，即 200 帧 567² 画布的量：RGBA 约 260MB，
# 内存、耗时、文件大小都按它封顶
ROTATE_MAX_PIXELS = 200 * 567**2


def _canvas_side(width: float, height: float) -> int:
    """能装下任意角度旋转的正方形画布边长"""
    return int(math.hypot(width, height)) + 2


def make_square_canvas(img: Image.Image, max_side: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    width, height = img.size
    diagonal = _canvas_side(width, height)
    canvas = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    offset_x = (diagonal - width) // 2
    offset_y = (diagonal - height) // 2
    rgba = img.convert("RGBA")
    canvas.paste(rgba, (offset_x, offset_y), rgba)
    return canvas


def _loop_plan(source_ms: int, circle_ms: int) -> tuple[int, int]:
    """原图放 m 遍、转 k 圈，两者都取整首尾的画面和角度才能同时接上。
    凑不齐时微调转速，不动原图节奏；总时长不超过两者较长者的 2 倍，
    此时转速偏差最坏约 ±33%"""

    def plan(repeats: int) -> tuple[int, int]:
        return repeats, max(1, round(repeats * source_ms / circle_ms))

    limit = 2 * max(source_ms, circle_ms) // source_ms
    return min(
        (plan(repeats) for repeats in range(1, limit + 1)),
        key=lambda p: abs(p[1] * circle_ms / (p[0] * source_ms) - 1),
    )


def _fit_budget(
    frames: list[Image.Image], durations: list[int], repeats: int
) -> tuple[int, int, list[Image.Image], list[int]]:
    """先缩边长，缩到下限还超才合并短帧降帧；返回 (最长边, 旋转步长, 帧, 时长)"""
    width, height = frames[0].size
    # 原图本身比下限还小就不缩，直接降帧
    min_side = min(ROTATE_MIN_SIDE, max(width, height))
    interval = GIF_MIN_DURATION_MS
    while True:
        new_frames, new_durations = retime_frames_for_speed(
            frames, durations, 1.0, min_duration_ms=interval
        )
        step_ms = max(ROTATE_STEP_MS, interval)
        count = repeats * sum(len(_split_ms(d, step_ms)) for d in new_durations)
        # 预算允许的画布边长，换算回原图最长边
        max_canvas = math.isqrt(ROTATE_MAX_PIXELS // count)
        max_side = min(
            ROTATE_MAX_SIDE,
            int((max_canvas - 2) * max(width, height) / math.hypot(width, height)),
        )
        if max_side >= min_side:
            return max_side, step_ms, new_frames, new_durations
        interval += 10


def _split_ms(duration: int, step_ms: int) -> list[int]:
    """长帧均分成接近步长的几段（每段在 0.75～1.5 倍步长之间），切点对齐 GIF 的 10ms 刻度。
    用四舍五入而不是向上取整：降帧时短帧刚被并到步长以上，不能再被切成两半"""
    pieces = max(1, round(duration / step_ms))
    cuts = [round(duration * i / pieces / 10) * 10 for i in range(pieces + 1)]
    return [end - start for start, end in zip(cuts, cuts[1:])]


def _process_rotate_file(
    image_path: str, animated: bool, direction: str, speed: float
) -> str:
    circle_ms = round(ROTATE_CIRCLE_MS / speed / 10) * 10
    if animated:
        frames, durations, _ = load_gif_frames(image_path)
    else:
        # 静态图当成一帧、正好转一圈的动图
        frames, durations = [open_static_image(image_path)], [circle_ms]

    # 先对齐 GIF 的 10ms 刻度，总时长才能和圈数严格对上
    frames, durations = retime_frames_for_speed(frames, durations, 1.0)
    source_ms = sum(durations)
    repeats, circles = _loop_plan(source_ms, circle_ms)
    max_side, step_ms, frames, durations = _fit_budget(frames, durations, repeats)
    # 原图每帧都保留、长帧再按步长切段，角度按时刻算：转速和切法无关，结尾正好转满整圈
    actual_circle_ms = repeats * source_ms / circles
    sign = -1 if direction == "clockwise" else 1

    output_frames = []
    output_durations = []
    elapsed = 0
    for _ in range(repeats):
        for frame, duration in zip(frames, durations):
            # 预乘 alpha 后再插值，否则透明角落的黑色会混进边缘
            canvas = make_square_canvas(frame, max_side).convert("RGBa")
            for piece in _split_ms(duration, step_ms):
                angle = sign * 360 * elapsed / actual_circle_ms
                output_frames.append(
                    canvas.rotate(angle, resample=Image.Resampling.BICUBIC).convert(
                        "RGBA"
                    )
                )
                output_durations.append(piece)
                elapsed += piece

    logger.debug(
        f"旋转参数: 倍速{speed}, 原图{len(frames)}帧/{source_ms}ms × {repeats}遍, "
        f"转{circles}圈（每圈{actual_circle_ms:.0f}ms，目标{circle_ms}ms）, "
        f"最长边{max_side}, 步长{step_ms}ms, 输出{len(output_frames)}帧"
    )

    path = output_path(f"rotate_{direction}", "gif")
    save_gif(output_frames, path, durations=output_durations, loop=0)
    return str(path)


async def process_image_rotate(image_url: str, direction: str, speed: float) -> str:
    async with download_image(image_url) as (image_path, animated):
        return await run_heavy(
            _process_rotate_file, image_path, animated, direction, speed
        )
