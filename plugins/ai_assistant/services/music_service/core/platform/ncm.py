from __future__ import annotations

from typing import ClassVar

from nonebot.log import logger

from ..model import Platform, Song
from .base import BaseMusicPlayer


class NetEaseMusic(BaseMusicPlayer):
    """
    网易云音乐（Web API）
    """

    platform: ClassVar[Platform] = Platform(
        name="netease",
        display_name="网易云音乐",
        keywords=["网易云", "网易点歌"],
    )

    async def fetch_songs(self, keyword: str, limit: int = 5, extra: str | None = None) -> list[Song]:
        result = await self._request(
            url="http://music.163.com/api/search/get/web",
            method="POST",
            data={"s": keyword, "limit": limit, "type": 1, "offset": 0},
            cookies={"appver": "2.0.2"},
        )
        # 检查响应格式是否有效
        if not isinstance(result, dict) or "result" not in result:
            logger.warning(f"[music_plugin] NetEaseMusic API响应格式异常：{result}")
            return []

        # 当搜索结果为空时（songCount=0），API不返回 songs 字段，这是正常情况
        if "songs" not in result["result"]:
            logger.debug(f"[music_plugin] NetEaseMusic 搜索无结果：{keyword}")
            return []

        songs = result["result"]["songs"][:limit]

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
