# gif_speed.py
import os

from nonebot import logger

from utils.concurrency import run_in_pool
from plugins.image_processor.utils import (
    UserInputError,
    download_image,
    load_gif_frames,
    retime_frames_for_speed,
    save_gif,
)
from utils.files import ensure_output_dir


def _change_gif_speed_file(image_path: str, speed_factor: float) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功提取到任何帧")

    new_frames, new_durations = retime_frames_for_speed(frames, durations, speed_factor)

    output_dir = ensure_output_dir("nonebot_gif_speed")
    output_path = output_dir / f"speed_{speed_factor}x_{os.urandom(4).hex()}.gif"

    save_gif(
        new_frames, output_path, durations=new_durations, loop=int(meta.get("loop", 0))
    )

    logger.debug(
        f"GIF倍速处理成功: {len(frames)}帧 -> {len(new_frames)}帧, "
        f"原时长={sum(durations)}ms, 新时长={sum(new_durations)}ms, 倍速={speed_factor}"
    )
    return str(output_path)


async def change_gif_speed(image_url: str, speed_factor: float) -> str:
    async with download_image(image_url) as (image_path, animated):
        if not animated:
            raise UserInputError("回复的图片不是 GIF 动图")
        return await run_in_pool(_change_gif_speed_file, image_path, speed_factor)
