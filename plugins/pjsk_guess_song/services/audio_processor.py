# pjsk_guess_song/services/audio_processor.py
"""
(新文件)
音频处理器
只负责 FFmpeg 和 Pydub 的核心音频操作。
"""

import asyncio
import io
import re
import os
import subprocess
import aiohttp
import random
from typing import Optional, Tuple, Union, Dict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from nonebot.log import logger

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    AudioSegment = None
    PYDUB_AVAILABLE = False
    logger.warning("Pydub not installed, audio processing features will be limited.")

from .cache_service import CacheService


class AudioProcessor:
    def __init__(self, cache_service: CacheService, output_dir: Path, executor: ThreadPoolExecutor):
        self.cache_service = cache_service
        self.output_dir = output_dir
        self.executor = executor
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> Optional[aiohttp.ClientSession]:
        """延迟初始化并获取 aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_segment_mean_dbfs_ffmpeg(self, file_path: Union[Path, str], start_s: float, duration_s: float) -> Optional[float]:
        """[异步] 使用ffmpeg快速检测指定音频片段的平均音量(dBFS)。"""
        command = [
            'ffmpeg', '-hide_banner', '-ss', str(start_s), '-t', str(duration_s),
            '-i', str(file_path), '-af', 'volumedetect', '-f', 'null', '-'
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr_bytes = await proc.communicate()
            stderr_str = stderr_bytes.decode('utf-8', errors='ignore')

            match = re.search(r"mean_volume:\s*(-?[\d\.]+)\s*dB", stderr_str)
            if match:
                return float(match.group(1))

            logger.warning(f"无法从ffmpeg输出中解析mean_volume: {stderr_str}")
            return -999.0
        except FileNotFoundError:
            logger.error("ffmpeg 未安装或不在系统路径中。无法执行静音检测。")
            raise  # 抛出异常，让上层处理
        except Exception as e:
            logger.error(f"执行ffmpeg volumedetect时出错: {e}")
            return -999.0

    def get_duration_ms_ffprobe_sync(self, file_path: Union[Path, str]) -> Optional[float]:
        """[同步] 使用 ffprobe 高效获取音频时长。"""
        command = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of',
                   'default=noprint_wrappers=1:nokey=1', str(file_path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
            return float(result.stdout.strip()) * 1000
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as e:
            logger.error(f"使用 ffprobe 获取时长失败 ({type(e).__name__}): {e}")
            return None

    def process_audio_with_pydub(self, audio_data: Union[str, Path, io.BytesIO], audio_format: str, options: dict) -> Optional['AudioSegment']:
        """[同步] 在线程池中执行的同步pydub处理逻辑"""
        if not PYDUB_AVAILABLE:
            logger.error("Pydub 未安装，无法执行慢速路径处理。")
            return None
        try:
            audio = AudioSegment.from_file(audio_data, format=audio_format)
            preprocessed_mode = options.get("preprocessed_mode")
            if preprocessed_mode == "bass_only": audio += 6
            target_duration_ms = int(options.get("target_duration_seconds", 10) * 1000)
            if preprocessed_mode in ["bass_only", "drums_only"]: target_duration_ms *= 2
            speed_multiplier = options.get("speed_multiplier", 1.0)
            source_duration_ms = int(target_duration_ms * speed_multiplier)
            total_duration_ms = len(audio)

            if source_duration_ms >= total_duration_ms:
                clip_segment = audio
            else:
                forced_start_ms = options.get("force_start_ms")
                if forced_start_ms is not None:
                    start_ms = forced_start_ms
                else:
                    start_range_min = 0
                    if not preprocessed_mode and not options.get("is_piano_mode"):
                        start_range_min = int(options.get("song_filler_sec", 0) * 1000)
                    start_range_max = total_duration_ms - source_duration_ms
                    start_ms = random.randint(start_range_min,
                                              start_range_max) if start_range_min < start_range_max else start_range_min

                end_ms = start_ms + source_duration_ms
                clip_segment = audio[start_ms:end_ms]

            clip = clip_segment
            if speed_multiplier != 1.0:
                clip = clip._spawn(clip.raw_data, overrides={'frame_rate': int(clip.frame_rate * speed_multiplier)})
            if options.get("reverse_audio", False):
                clip = clip.reverse()
            band_pass = options.get("band_pass")
            if band_pass and isinstance(band_pass, tuple) and len(band_pass) == 2:
                low_freq, high_freq = band_pass
                clip = clip.high_pass_filter(low_freq).low_pass_filter(high_freq) + 6
            return clip
        except Exception as e:
            logger.error(f"Pydub processing in executor failed: {e}", exc_info=True)
            return None

    async def process_anvo_audio(self, song: Dict, vocal_info: Dict) -> Optional[str]:
        """处理ANVO音频，优先使用缓存文件。"""
        char_ids = [c.get('characterId') for c in vocal_info.get('characters', [])]
        char_id_for_cache = '_'.join(map(str, sorted(char_ids)))
        output_filename = f"anvo_{song['id']}_{char_id_for_cache}.mp3"
        output_path = self.output_dir / output_filename

        if output_path.exists():
            logger.info(f"使用已缓存的ANVO文件: {output_filename}")
            return str(output_path)

        logger.info(f"缓存文件 {output_filename} 不存在，正在创建...")
        mp3_source = self.cache_service.get_resource_path_or_url(
            f"songs/{vocal_info['vocalAssetbundleName']}/{vocal_info['vocalAssetbundleName']}.mp3")
        if not mp3_source:
            logger.error("找不到有效的ANVO音频文件。")
            return None

        filler_sec = song.get('fillerSec', 0)
        command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(filler_sec), '-i', str(mp3_source),
                   '-c:a', 'copy', '-f', 'mp3', str(output_path)]

        try:
            proc = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"FFmpeg failed. Stderr: {stderr.decode(errors='ignore')}")
                if output_path.exists(): os.remove(output_path)
                return None
            return str(output_path)
        except Exception as e:
            logger.error(f"FFmpeg执行失败: {e}", exc_info=True)
            return None

    async def clip_audio_ffmpeg_fast(self, audio_source: Union[Path, str], output_path: Path, start_ms: float, duration_s: float) -> bool:
        """[新] 使用 FFmpeg -c copy 快速裁剪音频"""
        command = [
            'ffmpeg', '-ss', str(start_ms / 1000.0), '-i', str(audio_source),
            '-t', str(duration_s), '-c', 'copy', '-y', str(output_path)
        ]
        try:
            run_subprocess = partial(subprocess.run, command, capture_output=True, text=True, check=True, encoding='utf-8')
            result = await asyncio.get_running_loop().run_in_executor(self.executor, run_subprocess)
            if result.returncode != 0:
                logger.warning(f"ffmpeg clipping failed: {result.stderr}")
                return False
            return True
        except Exception as e:
            logger.warning(f"ffmpeg clipping exception: {e}")
            return False

    async def get_audio_data(self, audio_source: Union[Path, str]) -> Optional[Union[str, Path, io.BytesIO]]:
        """[新] 智能获取音频数据，如果是URL则下载到内存"""
        try:
            if isinstance(audio_source, str) and audio_source.startswith(('http://', 'https://')):
                session = await self._get_session()
                if not session:
                    logger.error("无法获取 aiohttp session")
                    return None
                async with session.get(audio_source) as response:
                    response.raise_for_status()
                    return io.BytesIO(await response.read())
            else:
                return audio_source
        except Exception as e:
            logger.error(f"获取音频数据 {audio_source} 失败: {e}")
            return None

    async def terminate(self):
        """关闭 aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()
