"""PJSK 猜歌 / 听歌。玩法来自已归档的 astrbot_plugin_pjsk_guess_song，GPL-3.0"""

import asyncio

from nonebot import get_driver, logger

from core.lifecycle import on_plugin_startup
from plugins.pjsk_guess_song import songs, store


@on_plugin_startup(get_driver(), "pjsk_guess_song")
async def _on_startup():
    await store.init()
    await asyncio.to_thread(songs.load)
    stats = songs.stats()
    logger.info(
        f"PJSK 猜歌已启动：{stats['songs']} 首歌曲、{stats['anvo']} 首 Another Vocal、"
        f"{stats['aliased']} 首有别名"
    )


from plugins.pjsk_guess_song import commands  # noqa: F401
