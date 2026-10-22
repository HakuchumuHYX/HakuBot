"""
戳一戳插件
戳 bot 随机回一条本群投稿（文本或图片）；@bot 投稿 增加内容；长文本可按群转成图片发送。
"""

from nonebot import get_driver

from core.lifecycle import on_plugin_startup
from plugins.poke_reply import store


@on_plugin_startup(get_driver(), "poke_reply")
def _on_startup() -> None:
    store.load()


from plugins.poke_reply import admin, handlers  # noqa: F401
