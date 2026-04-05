"""
PJSK 猜卡面插件
用户发送 /pjsk猜卡面 开始游戏，bot 发送一张随机裁剪的卡面图片，
用户通过发送角色昵称来猜测卡面属于哪个角色。

采用消息队列模式：游戏主循环在一个协程中完成，
on_message 监听器将消息事件放入队列，主循环通过 wait_for 实现超时。
"""
import asyncio
import time
from typing import Dict, Set

from nonebot import on_command, on_message, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    Message,
)
from nonebot.exception import MatcherException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import plugin_config, CARD_IMAGES_DIR
from .card_data import (
    load_cards, random_card, get_card_image_url, get_card_title, get_card_hint,
    get_local_card_path, get_all_download_tasks,
)
from .nickname import get_cid_by_nickname
from .image_utils import (
    download_image, load_local_image, batch_download_images,
    get_dir_size_mb, random_crop_image, image_to_bytes,
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

# 群ID -> 消息队列（游戏进行中时存在）
guess_msg_queues: Dict[int, asyncio.Queue] = {}


# ==================== 启动时加载数据 ==================== #

driver = get_driver()


@driver.on_startup
async def _on_startup():
    load_cards()
    logger.info("PJSK 猜卡面插件已启动")


# ==================== 数据获取命令（SUPERUSER） ==================== #

fetch_card_data_cmd = on_command("猜卡面数据获取", permission=SUPERUSER, priority=5, block=True)


@fetch_card_data_cmd.handle()
async def handle_fetch_card_data(bot: Bot, event: MessageEvent, matcher: Matcher):
    """SUPERUSER 专用：批量下载所有卡面图片到本地"""
    await bot.send(event, "开始获取猜卡面数据，正在生成下载任务列表...")

    try:
        tasks = get_all_download_tasks()
    except Exception as e:
        logger.error(f"[猜卡面数据获取] 生成下载任务失败: {e}", exc_info=True)
        await matcher.finish(f"生成下载任务失败: {e}")
        return

    total = len(tasks)
    if total == 0:
        await matcher.finish("没有需要下载的卡面图片。")
        return

    await bot.send(event, f"共 {total} 张卡面图片待处理，开始下载...\n（每 50 张报告一次进度）")

    async def progress_callback(done: int, total: int, success: int, skipped: int, failed: int):
        await bot.send(event, f"📥 下载进度: {done}/{total}\n✅ 成功: {success} | ⏭️ 跳过(已存在): {skipped} | ❌ 失败: {failed}")

    try:
        result = await batch_download_images(
            tasks=tasks,
            concurrency=5,
            progress_callback=progress_callback,
        )
    except MatcherException:
        raise
    except Exception as e:
        logger.error(f"[猜卡面数据获取] 批量下载异常: {e}", exc_info=True)
        await matcher.finish(f"批量下载过程中出错: {e}")
        return

    dir_size = get_dir_size_mb(CARD_IMAGES_DIR)

    report = (
        f"🎉 猜卡面数据获取完成！\n"
        f"📊 总计: {result['total']} 张\n"
        f"✅ 成功下载: {result['success']} 张\n"
        f"⏭️ 跳过(已存在): {result['skipped']} 张\n"
        f"❌ 下载失败: {result['failed']} 张\n"
        f"💾 数据目录大小: {dir_size:.1f} MB"
    )

    if result['failed'] > 0 and result.get('failed_urls'):
        failed_sample = result['failed_urls'][:5]
        report += "\n\n失败示例:\n" + "\n".join(failed_sample)
        if len(result['failed_urls']) > 5:
            report += f"\n...等共 {len(result['failed_urls'])} 个"

    await bot.send(event, report)


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
    if group_id in guess_msg_queues:
        await matcher.finish("当前已有猜卡面游戏正在进行中！")

    # 随机选卡并加载图片（失败自动重试，最多 3 次）
    MAX_RETRY = 3
    card = None
    after_training = False
    card_image = None

    for attempt in range(MAX_RETRY):
        try:
            card, after_training = random_card()
        except RuntimeError as e:
            await matcher.finish(str(e))
            return

        local_path = get_local_card_path(card, after_training)
        url = get_card_image_url(card, after_training)
        logger.info(f"[猜卡面] 群 {group_id} 第 {attempt + 1} 次尝试: card_id={card['id']}")

        # 优先本地加载
        if local_path.exists():
            card_image = load_local_image(local_path)
            if card_image:
                logger.debug(f"[猜卡面] 从本地加载卡面成功: {local_path}")
                break
            else:
                logger.warning(f"[猜卡面] 本地文件存在但加载失败: {local_path}")

        # fallback 到在线下载
        try:
            card_image = await download_image(url)
            logger.debug(f"[猜卡面] 在线下载卡面成功: {url}")
            break
        except Exception as e:
            logger.warning(f"[猜卡面] 第 {attempt + 1} 次下载失败，重新选卡: {e}")
            card_image = None
            continue

    if card_image is None:
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
    title = get_card_title(card, after_training)

    # 发送裁剪图
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
