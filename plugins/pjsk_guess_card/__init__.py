"""
PJSK 猜卡面插件
用户发送 /pjsk猜卡面 开始游戏，bot 发送一张随机裁剪的卡面图片，
用户通过发送角色昵称来猜测卡面属于哪个角色。

采用消息队列模式：游戏主循环在开局命令的 handler 里走完，
on_message 监听器将消息事件放入队列，主循环通过 wait_for 实现超时。
"""

import asyncio
import time

from nonebot import get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    GROUP,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.matcher import Matcher
from PIL import Image

from core.access import switch_rule
from core.lifecycle import on_plugin_startup, runtime
from utils.images.formats import image_to_bytes
from utils.logging import get_exc_desc
from utils.network.http import download_image
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from plugins.pjsk_guess_card import cards
from plugins.pjsk_guess_card.config import plugin_config

HINT_WORD = "提示"
STOP_WORDS = {"结束猜卡", "停止猜卡"}
# 随机到的卡在资源站下载失败时换一张（新卡可能还没上传）
CARD_ATTEMPTS = 3
# 下载超过这么久才提示“正在抽卡”
PREPARING_NOTICE_DELAY = 1.5

# 群ID -> 消息队列（游戏进行中时存在）
_queues: dict[int, asyncio.Queue[GroupMessageEvent]] = {}
# 正在抽卡/下载图片但还未开始倒计时的群
_preparing: set[int] = set()


@on_plugin_startup(get_driver(), "pjsk_guess_card")
def _on_startup() -> None:
    cards.load()
    logger.info(f"PJSK 猜卡面已启动：{cards.count()} 张可用卡牌（3星/4星/生日卡）")


async def _draw_card() -> tuple[dict, str, Image.Image] | None:
    """随机选卡并下载卡面，返回 (卡牌数据, 卡图类型, 卡面)"""
    for attempt in range(1, CARD_ATTEMPTS + 1):
        card, image_type = cards.random_card()
        try:
            image = await download_image(cards.image_url(card, image_type))
            return card, image_type, image
        except Exception as e:
            logger.info(
                f"[猜卡面] 第 {attempt} 次下载卡面失败，重新选卡: "
                f"card_id={card['id']}, {get_exc_desc(e)}"
            )
    return None


async def _play(
    matcher: Matcher, queue: asyncio.Queue, card: dict, title: str
) -> Message:
    """等群友作答直到猜对、手动结束或超时，返回揭晓答案的那条消息"""
    used_hints: set[str] = set()
    guessed_cids: set[int] = set()
    end_time = time.time() + plugin_config.guess_timeout
    while True:
        try:
            event: GroupMessageEvent = await asyncio.wait_for(
                queue.get(), timeout=end_time - time.time()
            )
        except asyncio.TimeoutError:
            return Message(f"时间到！\n正确答案：\n{title}")

        text = event.get_plaintext().strip()
        if text in STOP_WORDS:
            logger.info(f"[猜卡面] 群 {event.group_id} 手动结束 user={event.user_id}")
            return Message(f"猜卡面已手动结束！\n正确答案：\n{title}")
        if text == HINT_WORD:
            await matcher.send(cards.next_hint(card, used_hints) or "没有更多提示了！")
            continue

        # 同一个角色只算一次
        cid = cards.character_by_nickname(text)
        if cid is None or cid in guessed_cids:
            continue
        guessed_cids.add(cid)
        if cid == card["characterId"]:
            logger.info(f"[猜卡面] 群 {event.group_id} 猜对了 user={event.user_id}")
            return MessageSegment.reply(event.message_id) + f"猜对了！\n{title}"


# ==================== 猜卡面游戏主命令 ==================== #

guess_card_cmd = on_command(
    "pjsk猜卡面",
    rule=command_boundary & switch_rule("pjsk_guess_card"),
    permission=GROUP,
    priority=5,
    block=True,
)


@guess_card_cmd.handle()
async def handle_guess_card(event: GroupMessageEvent, matcher: Matcher):
    group_id = event.group_id
    if group_id in _queues or group_id in _preparing:
        await matcher.finish("当前已有猜卡面游戏正在进行中！")

    _preparing.add(group_id)
    try:
        draw_task = runtime.spawn(_draw_card(), name="pjsk_guess_card")
        try:
            drawn = await asyncio.wait_for(
                asyncio.shield(draw_task), timeout=PREPARING_NOTICE_DELAY
            )
        except asyncio.TimeoutError:
            await matcher.send("正在抽卡，请稍等...")
            drawn = await draw_task
        if drawn is None:
            await matcher.finish("多次尝试加载卡面图片均失败，请稍后再试")

        card, image_type, image = drawn
        title = cards.title(card, image_type)
        timeout = plugin_config.guess_timeout
        # 发送裁剪图；发送成功后才开始倒计时
        await matcher.send(
            image_segment(image_to_bytes(cards.crop(image)))
            + f"\n猜卡面开始！限时 {timeout} 秒"
            + "\n直接发送角色昵称/简称来猜测（如 ick, saki, miku）"
            + "\n发送「提示」获取提示，发送「结束猜卡」提前结束"
        )
        queue: asyncio.Queue[GroupMessageEvent] = asyncio.Queue()
        _queues[group_id] = queue
        _preparing.discard(group_id)
        logger.info(
            f"[猜卡面] 群 {group_id} 游戏开始: card_id={card['id']}, timeout={timeout}s"
        )

        await matcher.send(await _play(matcher, queue, card, title))
        await matcher.send(image_segment(image_to_bytes(image)))
    finally:
        _preparing.discard(group_id)
        _queues.pop(group_id, None)


# ==================== 消息监听（将消息放入队列） ==================== #

answer_listener = on_message(priority=3, block=False)


@answer_listener.handle()
async def handle_answer(event: GroupMessageEvent):
    queue = _queues.get(event.group_id)
    if queue is not None:
        queue.put_nowait(event)
