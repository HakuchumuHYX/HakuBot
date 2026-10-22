"""从资源站下载曲目，用 ffmpeg 截取片段或裁掉前奏；每次在临时目录里处理完，返回 mp3 数据"""

import asyncio
import json
import random
import tempfile
from pathlib import Path

from utils.network.http import download_to_file
from plugins.pjsk_guess_song.songs import song_url


async def _run(*args: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"{args[0]} 退出码 {proc.returncode}: {stderr.decode(errors='ignore')[-500:]}"
        )
    return stdout


async def _probe(path: Path) -> tuple[float, int]:
    """(时长秒, 采样率)"""
    out = await _run(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate:format=duration",
        "-of",
        "json",
        str(path),
    )
    info = json.loads(out)
    return float(info["format"]["duration"]), int(info["streams"][0]["sample_rate"])


async def clip(
    bundle: str, start_min_s: float, duration_s: float, speed: float = 1.0
) -> bytes:
    """从 start_min_s 之后随机截一段，播放时长 duration_s。

    speed≠1 时改采样率变速，音调跟着变（和原来 pydub 的做法一致）。
    """
    with tempfile.TemporaryDirectory(prefix="pjsk_gs_") as tmp:
        src, out = Path(tmp) / "src.mp3", Path(tmp) / "out.mp3"
        await download_to_file(song_url(bundle), src)
        total_s, sample_rate = await _probe(src)

        source_s = duration_s * speed
        low, high = int(start_min_s * 1000), int((total_s - source_s) * 1000)
        start_s = (random.randint(low, high) if low < high else low) / 1000

        if speed == 1.0:
            codec = ["-c", "copy"]
        else:
            codec = [
                "-af",
                f"asetrate={int(sample_rate * speed)},aresample={sample_rate}",
                "-b:a",
                "128k",
            ]
        await _run(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(start_s), "-t", str(source_s), "-i", str(src),
            *codec, "-y", str(out),
        )  # fmt: skip
        return out.read_bytes()


async def full_track(bundle: str, skip_s: float) -> bytes:
    """整首，裁掉开头 skip_s 秒的空白"""
    with tempfile.TemporaryDirectory(prefix="pjsk_gs_") as tmp:
        src, out = Path(tmp) / "src.mp3", Path(tmp) / "out.mp3"
        await download_to_file(song_url(bundle), src)
        await _run(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(skip_s), "-i", str(src), "-c:a", "copy", "-y", str(out),
        )  # fmt: skip
        return out.read_bytes()
