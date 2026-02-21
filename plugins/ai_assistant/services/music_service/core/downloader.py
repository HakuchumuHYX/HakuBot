from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import aiofiles
import aiohttp
from nonebot.log import logger

from ..types import MusicServiceConfig


class Downloader:
    """下载器（用于 file 模式下载音频到本地缓存）"""

    def __init__(self, cfg: MusicServiceConfig, songs_dir: Path):
        self.cfg = cfg
        self.songs_dir = songs_dir
        proxy = self.cfg.proxy or None
        # 下载超时设置：总超时使用配置值，连接超时固定10秒
        timeout = aiohttp.ClientTimeout(total=cfg.timeout, connect=10)
        self.session = aiohttp.ClientSession(proxy=proxy, timeout=timeout)

    async def initialize(self) -> None:
        if self.cfg.clear_cache:
            self._ensure_cache_dir()
        else:
            self.songs_dir.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        if not self.session.closed:
            await self.session.close()

    def _ensure_cache_dir(self) -> None:
        """重建缓存目录：存在则清空，不存在则新建"""
        if self.songs_dir.exists():
            shutil.rmtree(self.songs_dir)
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[music_plugin] 缓存目录已重建：{self.songs_dir}")

    async def download_song(self, url: str) -> Path | None:
        """下载歌曲，返回保存路径"""
        song_uuid = uuid.uuid4().hex
        file_path = self.songs_dir / f"{song_uuid}.mp3"
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    logger.error(f"[music_plugin] 歌曲下载失败，HTTP 状态码：{response.status}")
                    return None

                async with aiofiles.open(file_path, "wb") as f:
                    async for chunk in response.content.iter_chunked(1024):
                        await f.write(chunk)

            logger.debug(f"[music_plugin] 歌曲下载完成：{file_path}")
            return file_path

        except Exception as e:
            logger.error(f"[music_plugin] 歌曲下载失败：{e}")
            return None
