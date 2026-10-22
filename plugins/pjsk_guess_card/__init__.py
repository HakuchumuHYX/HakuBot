"""
PJSK 猜卡面插件
用户发送 /pjsk猜卡面 开始游戏，bot 发送一张随机裁剪的卡面图片，
用户通过发送角色昵称来猜测卡面属于哪个角色；发送 看<昵称> 随机看一张该角色的卡面。
"""

from nonebot import get_driver, logger

from core.lifecycle import on_plugin_startup
from plugins.pjsk_guess_card import cards


@on_plugin_startup(get_driver(), "pjsk_guess_card")
def _on_startup() -> None:
    cards.load()
    logger.info(f"PJSK 猜卡面已启动：{cards.count()} 张可用卡牌（3星/4星/生日卡）")


from plugins.pjsk_guess_card import handlers  # noqa: F401
