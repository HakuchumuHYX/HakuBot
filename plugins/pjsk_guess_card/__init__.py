"""
PJSK 猜卡面插件
用户发送 /pjsk猜卡面 开始游戏，bot 发送一张随机裁剪的卡面图片，
用户通过发送角色昵称来猜测卡面属于哪个角色。

采用消息队列模式：游戏主循环在一个协程中完成，
on_message 监听器将消息事件放入队列，主循环通过 wait_for 实现超时。
"""
import asyncio
import time
from typing import Dict, Optional, Set, Tuple

from nonebot import on_command, on_message, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageSegment,
)
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from PIL import Image

from .config import plugin_config
from .card_data import (
    CardImageType,
    load_cards, random_card, get_card_image_url, get_card_title, get_card_hint,
)
from .nickname import get_cid_by_nickname
from .image_utils import (
    download_image, random_crop_image, image_to_bytes,
)
from ..plugin_manager.enable import is_plugin_enabled

PLUGIN_ID = "pjsk_guess_card"

__plugin_meta__ = PluginMetadata(
    name="PJSK猜卡面",
    description="Project Sekai 猜卡面娱乐功能",
    usage="/pjsk猜卡面 - 开始猜卡面游戏",
)

HINT_KEYWORDS = ("提示",)
STOP_KEYWORDS = ("结束猜卡", "停止猜卡")
PREPARING_NOTICE_DELAY = 1.5

# 群ID -> 消息队列（游戏进行中时存在）
guess_msg_queues: Dict[int, asyncio.Queue] = {}

# 正在抽卡/下载图片但还未开始倒计时的群
preparing_guess_groups: Set[int] = set()


# ==================== 启动时加载数据 ==================== #

driver = get_driver()


@driver.on_startup
async def _on_startup():
    load_cards()
    logger.info("PJSK 猜卡面插件已启动")


async def _prepare_random_card_image(
    group_id: int,
    max_retry: int,
) -> Tuple[Optional[dict], Optional[CardImageType], Optional[Image.Image]]:
    """随机选卡并从远程加载卡图。"""
    for attempt in range(max_retry):
        card, image_type = random_card()
        url = get_card_image_url(card, image_type)
        logger.info(
            f"[猜卡面] 群 {group_id} 第 {attempt + 1} 次尝试: "
            f"card_id={card['id']}, image_type={image_type}"
        )

        try:
            card_image = await download_image(url)
            logger.debug(f"[猜卡面] 在线下载卡面成功: {url}")
            return card, image_type, card_image
        except Exception as e:
            logger.warning(f"[猜卡面] 第 {attempt + 1} 次下载失败，重新选卡: {e}")

    return None, None, None


# ==================== 猜卡面游戏主命令 ==================== #

guess_card_cmd = on_command("pjsk猜卡面", priority=5, block=True)


@guess_card_cmd.handle()
async def handle_guess_card(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    group_id = event.group_id
    user_id = str(event.user_id)

    # 检查插件是否启用
    if not is_plugin_enabled(PLUGIN_ID, str(group_id), user_id):
        await matcher.finish()

    # 检查是否有进行中的游戏
    if group_id in guess_msg_queues or group_id in preparing_guess_groups:
        await matcher.finish("当前已有猜卡面游戏正在进行中！")

    preparing_guess_groups.add(group_id)
    try:
        try:
            prepare_task = asyncio.create_task(_prepare_random_card_image(group_id, 3))
            try:
                card, image_type, card_image = await asyncio.wait_for(
                    asyncio.shield(prepare_task),
                    timeout=PREPARING_NOTICE_DELAY,
                )
            except asyncio.TimeoutError:
                await bot.send(event, "正在抽卡，请稍等...")
                card, image_type, card_image = await prepare_task
        except RuntimeError as e:
            await matcher.finish(str(e))
            return

        if card is None or image_type is None or card_image is None:
            await matcher.finish("多次尝试加载卡面图片均失败，请稍后再试")
            return

        # 准备图片数据
        full_image_bytes = image_to_bytes(card_image)
        cropped = random_crop_image(
            card_image,
            rate_min=plugin_config.crop_rate_min,
            rate_max=plugin_config.crop_rate_max,
        )
        cropped_bytes = image_to_bytes(cropped)
        title = get_card_title(card, image_type)

        # 发送裁剪图；发送成功后才开始倒计时
        timeout = plugin_config.guess_timeout
        msg = (
            MessageSegment.image(cropped_bytes)
            + f"\n猜卡面开始！限时 {timeout} 秒"
            + "\n直接发送角色昵称/简称来猜测（如 ick, saki, miku）"
            + "\n发送「提示」获取提示，发送「结束猜卡」提前结束"
        )
        await bot.send(event, msg)

        # 创建消息队列，开始游戏主循环
        queue: asyncio.Queue[GroupMessageEvent] = asyncio.Queue()
        guess_msg_queues[group_id] = queue
        preparing_guess_groups.discard(group_id)

        used_hints: Set[str] = set()
        guessed_cids: Set[int] = set()
        end_time = time.time() + timeout

        logger.info(f"[猜卡面] 群 {group_id} 游戏开始: card_id={card['id']}, timeout={timeout}s")

        try:
            while True:
                rest = end_time - time.time()
                if rest <= 0:
                    # 超时
                    logger.info(f"[猜卡面] 群 {group_id} 超时")
                    await bot.send(event, f"时间到！\n正确答案：\n{title}")
                    await bot.send(event, MessageSegment.image(full_image_bytes))
                    return

                try:
                    msg_event: GroupMessageEvent = await asyncio.wait_for(
                        queue.get(), timeout=rest
                    )
                except asyncio.TimeoutError:
                    logger.info(f"[猜卡面] 群 {group_id} 超时")
                    await bot.send(event, f"时间到！\n正确答案：\n{title}")
                    await bot.send(event, MessageSegment.image(full_image_bytes))
                    return

                text = msg_event.get_plaintext().strip()
                if not text:
                    continue

                # 停止关键词
                if any(kw in text for kw in STOP_KEYWORDS):
                    logger.info(f"[猜卡面] 群 {group_id} 手动停止 user={msg_event.user_id}")
                    await bot.send(event, f"猜卡面已手动结束！\n正确答案：\n{title}")
                    await bot.send(event, MessageSegment.image(full_image_bytes))
                    return

                # 提示关键词
                if any(kw in text for kw in HINT_KEYWORDS):
                    hint = get_card_hint(card, used_hints)
                    if hint:
                        await bot.send(event, hint)
                    else:
                        await bot.send(event, "没有更多提示了！")
                    continue

                # 尝试解析为角色昵称
                cid = get_cid_by_nickname(text)
                if cid is None:
                    continue

                # 已猜过的角色跳过
                if cid in guessed_cids:
                    continue
                guessed_cids.add(cid)

                # 检查答案
                if cid == card["characterId"]:
                    logger.info(f"[猜卡面] 群 {group_id} 猜对了！ user={msg_event.user_id}, cid={cid}")
                    await bot.send(
                        event,
                        MessageSegment.reply(msg_event.message_id) + f"猜对了！\n{title}",
                    )
                    await bot.send(event, MessageSegment.image(full_image_bytes))
                    return
                else:
                    logger.debug(f"[猜卡面] 群 {group_id} 猜错 cid={cid}, guessed={len(guessed_cids)}")

        except Exception as e:
            logger.error(f"[猜卡面] 群 {group_id} 游戏主循环异常: {e}", exc_info=True)
            try:
                await bot.send(event, f"猜卡面出错了，正确答案：\n{title}")
                await bot.send(event, MessageSegment.image(full_image_bytes))
            except Exception:
                pass
    finally:
        preparing_guess_groups.discard(group_id)
        # 清理队列
        if group_id in guess_msg_queues:
            del guess_msg_queues[group_id]
        logger.info(f"[猜卡面] 群 {group_id} 游戏结束")


# ==================== 消息监听（将消息放入队列） ==================== #

answer_listener = on_message(priority=6, block=False)


@answer_listener.handle()
async def handle_answer(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    group_id = event.group_id
    queue = guess_msg_queues.get(group_id)
    if queue is not None:
        queue.put_nowait(event)
