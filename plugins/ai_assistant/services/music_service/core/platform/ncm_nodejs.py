from __future__ import annotations

from typing import ClassVar

from nonebot.log import logger

from ..model import Platform, Song
from .base import BaseMusicPlayer


class NetEaseMusicNodeJS(BaseMusicPlayer):
    """
    网易云音乐 NodeJS API
    """

    platform: ClassVar[Platform] = Platform(
        name="netease_nodejs",
        display_name="网易云NodeJS版",
        keywords=["nj点歌", "网易nj"],
    )

    async def fetch_songs(self, keyword: str, limit: int = 5, extra: str | None = None) -> list[Song]:
        result = await self._request(
            url=f"{self.cfg.nodejs_base_url}/search",
            method="POST",
            data={"keywords": keyword, "limit": limit, "type": 1, "offset": 0},
        )
        # 检查响应格式是否有效
        if not isinstance(result, dict) or "result" not in result:
            logger.warning(f"[music_plugin] NetEaseMusicNodeJS API响应格式异常：{result}")
            return []

        # 当搜索结果为空时（songCount=0），API不返回 songs 字段，这是正常情况
        if "songs" not in result["result"]:
            logger.debug(f"[music_plugin] NetEaseMusicNodeJS 搜索无结果：{keyword}")
            return []

        songs = result.get("result", {}).get("songs", [])[:limit]

        return [
            Song(
                id=str(s.get("id")),
                name=s.get("name"),
                artists="、".join(a["name"] for a in s.get("artists", [])),
                album=s.get("album", {}).get("name") if isinstance(s.get("album"), dict) else None,
                duration=s.get("duration"),
            )
            for s in songs
        ]

    async def fetch_comments(self, song: Song) -> Song:
        if song.comments:
            return song
        result = await self._request(
            url=f"{self.cfg.nodejs_base_url}/comment/hot",
            method="POST",
            data={"id": song.id, "type": 0},
        )
        # 检查响应格式是否有效
        if not isinstance(result, dict):
            logger.warning(f"[music_plugin] NetEaseMusicNodeJS 评论API响应格式异常：{result}")
            return song
        # 没有热评是正常情况，不需要记录警告
        if "hotComments" not in result:
            logger.debug(f"[music_plugin] NetEaseMusicNodeJS 歌曲无热评：{song.id}")
            return song
        if comments := result.get("hotComments"):
            song.comments = comments
        return song

    async def fetch_lyrics(self, song: Song) -> Song:
        if song.lyrics:
            return song
        result = await self._request(f"{self.cfg.nodejs_base_url}/lyric?id={song.id}")
        # 检查响应格式是否有效
        if not isinstance(result, dict):
            logger.warning(f"[music_plugin] NetEaseMusicNodeJS 歌词API响应格式异常：{result}")
            return song
        # 没有歌词是正常情况（纯音乐等），不需要记录警告
        if "lrc" not in result:
            logger.debug(f"[music_plugin] NetEaseMusicNodeJS 歌曲无歌词：{song.id}")
            return song
        lyric = result["lrc"].get("lyric") if isinstance(result.get("lrc"), dict) else None
        if lyric:
            song.lyrics = lyric
        return song

    async def fetch_extra(self, song: Song) -> Song:
        try:
            result = await self._request(
                url=f"{self.cfg.nodejs_base_url}/song/url?id={song.id}",
                method="GET",
            )
            if not isinstance(result, dict):
                logger.warning(f"[music_plugin] NetEaseMusicNodeJS 音频URL API响应格式异常：{result}")
                return song
        except Exception as e:
            logger.warning(f"{self.__class__.__name__} fetch_extra 失败: {e}")
            return song

        data = result.get("data")
        if not data:
            return song

        info = data[0]
        audio_url = info.get("url")
        if audio_url and song.audio_url is None:
            song.audio_url = audio_url

        return song
