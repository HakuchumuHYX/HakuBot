# video_to_gif.py
import asyncio
import json
import os
import random
import shutil
import urllib.parse

from nonebot import get_driver, logger

from core.lifecycle import on_plugin_startup
from plugins.image_processor.utils import (
    IMAGE_PROCESSOR_MAX_VIDEO_BYTES,
    IMAGE_PROCESSOR_VIDEO_DOWNLOAD_TIMEOUT,
    UserInputError,
    download_temp,
    output_path,
    run_command,
)
from utils.files import safe_delete_file

VIDEO_MAX_DURATION = 60
FFMPEG_TIMEOUT = 120
# 两个长视频同时转会抢 2 个核、一起撞超时，转码排队；
# 和 HEAVY_LOCK 分开，免得一个长转码堵住图片命令
VIDEO_LOCK = asyncio.Lock()


@on_plugin_startup(get_driver(), "image_processor:video_to_gif")
def _check_ffmpeg() -> None:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise RuntimeError("未找到 ffmpeg/ffprobe，视频转GIF不可用")


def _fix_ftn_url(url: str, file_name: str) -> str:
    """QQ 群文件直链需要非空的 fname 参数"""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if not query.get("fname", [""])[0]:
        query["fname"] = [file_name or f"video_{random.randint(10000, 99999)}.mp4"]
    return parsed._replace(query=urllib.parse.urlencode(query, doseq=True)).geturl()


async def get_video_info(video_path: str) -> tuple[float, float, int, int]:
    """返回 (时长秒, 帧率, 宽, 高)"""
    output = await run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate:format=duration",
            "-of",
            "json",
            video_path,
        ],
        timeout=FFMPEG_TIMEOUT,
        label="解析视频",
    )
    info = json.loads(output)
    if not info.get("streams"):
        raise UserInputError("文件里没有视频流")
    stream = info["streams"][0]
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise UserInputError("无法读取视频时长")
    num, _, den = stream.get("avg_frame_rate", "0/0").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    return duration, fps, int(stream["width"]), int(stream["height"])


def gif_parameters(
    duration: float, fps: float, width: int, height: int
) -> tuple[float, int]:
    """返回 (帧率, 目标长边)；越长的视频帧率越低、尺寸越小，控制 GIF 体积。
    帧率不低于 10：8fps 时动作画面看着卡，宁可体积大一些"""
    if duration <= 5:
        fps_cap = 20
    elif duration <= 15:
        fps_cap = 15
    elif duration <= 30:
        fps_cap = 12
    else:
        fps_cap = 10
    target_fps = max(1, min(fps_cap, fps)) if fps > 0 else fps_cap

    max_side = max(width, height)
    if max_side > 320:
        max_side = 320 if max_side <= 720 else 480
    return target_fps, max_side


async def convert_video_to_gif(
    video_url: str, file_name: str = "", expected_size: int = 0
) -> str:
    if expected_size > IMAGE_PROCESSOR_MAX_VIDEO_BYTES:
        raise UserInputError(
            f"视频文件过大（{expected_size // 1024 // 1024}MB），"
            f"上限 {IMAGE_PROCESSOR_MAX_VIDEO_BYTES // 1024 // 1024}MB"
        )

    # identity：后面要拿文件大小和消息里的 file_size 比对
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Encoding": "identity",
    }
    if "ftn.qq.com" in video_url:
        headers["Referer"] = "https://qun.qq.com/"
        video_url = _fix_ftn_url(video_url, file_name)

    result_path = output_path("video_gif", "gif")

    async with download_temp(
        video_url,
        ext="mp4",
        max_bytes=IMAGE_PROCESSOR_MAX_VIDEO_BYTES,
        timeout=IMAGE_PROCESSOR_VIDEO_DOWNLOAD_TIMEOUT,
        headers=headers,
        proxy=False,
    ) as video_path:
        size = os.path.getsize(video_path)
        # 失效的群文件链接也会返回 200，只能靠消息里带的大小识别
        if expected_size > 0 and size < expected_size * 0.9:
            raise UserInputError(
                f"下载的视频不完整（{size}/{expected_size} bytes），群文件链接可能已失效"
            )

        duration, source_fps, width, height = await get_video_info(video_path)
        if duration > VIDEO_MAX_DURATION:
            raise UserInputError(
                f"视频时长超过限制（{duration:.1f} 秒 > {VIDEO_MAX_DURATION} 秒），请选择较短的视频"
            )
        fps, max_side = gif_parameters(duration, source_fps, width, height)
        logger.debug(
            f"视频转GIF: 时长{duration:.1f}s, 原帧率{source_fps:.1f} -> {fps}, "
            f"原尺寸{width}x{height}, 长边 -> {max_side}"
        )

        # ffprobe 报的是旋转前的宽高，竖拍视频会带旋转标记；ffmpeg 解码时会先转正，
        # 所以只给长边、让它按转正后的画面等比缩放。先生成调色板再套用，画质比直接转好得多
        video_filter = (
            f"fps={fps},scale={max_side}:{max_side}"
            ":force_original_aspect_ratio=decrease:flags=lanczos,split[a][b];"
            "[a]palettegen=stats_mode=diff[p];"
            "[b][p]paletteuse=dither=sierra2:diff_mode=rectangle"
        )
        try:
            async with VIDEO_LOCK:
                await run_command(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        video_path,
                        "-filter_complex",
                        video_filter,
                        "-y",
                        str(result_path),
                    ],
                    timeout=FFMPEG_TIMEOUT,
                    label="ffmpeg 转码",
                )
        except BaseException:
            safe_delete_file(result_path)
            raise

    logger.debug(f"视频转GIF成功: {result_path}, 大小: {os.path.getsize(result_path)}")
    return str(result_path)
