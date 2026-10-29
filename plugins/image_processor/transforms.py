"""镜像、对称、倒放、倍速：下载 → 静态图处理一张 / 动图逐帧或改时序 → 保存"""

from nonebot import logger
from PIL import Image

from plugins.image_processor.utils import (
    UserInputError,
    convert_to_supported_mode,
    download_image,
    load_gif_frames,
    open_static_image,
    output_path,
    retime_frames_for_speed,
    run_heavy,
    save_gif,
)

_FLIP = Image.Transpose.FLIP_LEFT_RIGHT
# 行列互换：上下对称转成左右对称来做，再换回来
_SWAP = Image.Transpose.TRANSPOSE

MIRROR_METHODS = {
    "horizontal": _FLIP,
    "vertical": Image.Transpose.FLIP_TOP_BOTTOM,
}


def _left_symmetry(img: Image.Image) -> Image.Image:
    """左半边镜像到右半边；奇数宽时中间那列归左半"""
    width, height = img.size
    keep_w = (width + 1) // 2
    result = img.copy()
    if width - keep_w > 0:
        mirrored = img.crop((0, 0, width - keep_w, height)).transpose(_FLIP)
        result.paste(mirrored, (keep_w, 0))
    return result


def _right_symmetry(img: Image.Image) -> Image.Image:
    return _left_symmetry(img.transpose(_FLIP)).transpose(_FLIP)


def _top_symmetry(img: Image.Image) -> Image.Image:
    return _left_symmetry(img.transpose(_SWAP)).transpose(_SWAP)


def _bottom_symmetry(img: Image.Image) -> Image.Image:
    return _right_symmetry(img.transpose(_SWAP)).transpose(_SWAP)


SYMMETRY_METHODS = {
    "left": _left_symmetry,
    "right": _right_symmetry,
    "top": _top_symmetry,
    "bottom": _bottom_symmetry,
    # 左上四分之一铺满全图
    "center": lambda img: _top_symmetry(_left_symmetry(img)),
}


def _save_static(img: Image.Image, source_path: str, prefix: str) -> str:
    # 照片存成 PNG 体积是原图的好几倍：JPEG 进 JPEG 出，其余存 PNG 保住透明和清晰度
    with Image.open(source_path) as source:
        is_jpeg = source.format == "JPEG"
    path = output_path(prefix, "jpg" if is_jpeg else "png")
    if is_jpeg:
        img.save(path, "JPEG", quality=95)
    else:
        img.save(path, "PNG")
    return str(path)


def _save_frames(
    frames: list[Image.Image], durations: list[int], loop: int, prefix: str
) -> str:
    path = output_path(prefix, "gif")
    save_gif(frames, path, durations=durations, loop=loop)
    return str(path)


def _transform_file(image_path: str, animated: bool, frame_fn, prefix: str) -> str:
    if not animated:
        img = frame_fn(convert_to_supported_mode(open_static_image(image_path)))
        return _save_static(img, image_path, prefix)

    frames, durations, meta = load_gif_frames(image_path)
    # 原地替换：处理完的输入帧马上释放，峰值少一份
    for i, frame in enumerate(frames):
        frames[i] = frame_fn(frame)
    return _save_frames(frames, durations, meta["loop"], prefix)


def _sequence_file(image_path: str, sequence_fn, prefix: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    frames, durations = sequence_fn(frames, durations)
    return _save_frames(frames, durations, meta["loop"], prefix)


async def _transform(image_url: str, frame_fn, prefix: str) -> str:
    async with download_image(image_url) as (image_path, animated):
        return await run_heavy(_transform_file, image_path, animated, frame_fn, prefix)


async def _transform_sequence(image_url: str, sequence_fn, prefix: str) -> str:
    async with download_image(image_url) as (image_path, animated):
        if not animated:
            raise UserInputError("回复的图片不是 GIF 动图")
        return await run_heavy(_sequence_file, image_path, sequence_fn, prefix)


async def mirror_image(image_url: str, direction: str) -> str:
    method = MIRROR_METHODS[direction]
    return await _transform(image_url, lambda img: img.transpose(method), "mirror")


async def symmetry_image(image_url: str, symmetry_type: str) -> str:
    return await _transform(image_url, SYMMETRY_METHODS[symmetry_type], "symmetry")


async def reverse_gif(image_url: str) -> str:
    return await _transform_sequence(
        image_url,
        lambda frames, durations: (frames[::-1], durations[::-1]),
        "reversed",
    )


async def change_gif_speed(image_url: str, speed_factor: float) -> str:
    def retime(frames, durations):
        new_frames, new_durations = retime_frames_for_speed(
            frames, durations, speed_factor
        )
        logger.debug(
            f"GIF倍速: {len(frames)}帧 -> {len(new_frames)}帧, "
            f"{sum(durations)}ms -> {sum(new_durations)}ms, 倍速={speed_factor}"
        )
        return new_frames, new_durations

    return await _transform_sequence(image_url, retime, "speed")
