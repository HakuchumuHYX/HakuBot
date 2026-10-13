# gif_reverse.py
import os

from nonebot.log import logger

from utils.concurrency import run_in_pool
from plugins.image_processor.utils import download_image, load_gif_frames, save_gif
from utils.files import ensure_output_dir


def _reverse_gif_file(image_path: str) -> str:
    frames, durations, meta = load_gif_frames(image_path)
    if not frames:
        raise Exception("没有成功提取到任何帧")

    reversed_frames = list(reversed(frames))
    reversed_durations = list(reversed(durations))

    output_dir = ensure_output_dir("nonebot_gif_reverse")
    output_path = output_dir / f"reversed_{os.urandom(4).hex()}.gif"

    save_gif(
        reversed_frames,
        output_path,
        durations=reversed_durations,
        loop=int(meta.get("loop", 0)),
    )

    logger.info(f"GIF倒放成功: {len(frames)}帧")
    return str(output_path)


async def reverse_gif(image_url: str) -> str:
    async with download_image(image_url) as (image_path, animated):
        if not animated:
            raise Exception("回复的图片不是 GIF 动图")
        return await run_in_pool(_reverse_gif_file, image_path)
