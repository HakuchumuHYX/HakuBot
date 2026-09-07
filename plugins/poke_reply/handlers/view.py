import asyncio
from typing import List
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent, Bot
from nonebot.rule import to_me

from plugins.poke_reply.models.data import data_manager
from plugins.poke_reply.utils.common import ensure_at_me, create_forward_message
from plugins.poke_reply.config import get_group_image_dir
from utils.onebot.media import image_segment
from utils.onebot.forward import send_forward_msg

view_all_contributions = on_command(
    "查看所有投稿", rule=ensure_at_me() & to_me(), priority=5, block=True
)
view_all_texts = on_command(
    "查看所有文本", rule=ensure_at_me() & to_me(), priority=5, block=True
)
view_all_images = on_command(
    "查看所有图片", rule=ensure_at_me() & to_me(), priority=5, block=True
)

MAX_NODES_PER_FORWARD = 30
MAX_TEXT_LENGTH_PER_NODE = 20000
MAX_IMAGES_PER_BATCH = 15


async def send_text_forward_message(
    bot: Bot, group_id: int, texts: List[str], title: str = "文本投稿"
) -> bool:
    try:
        if not texts:
            await bot.send_group_msg(
                group_id=group_id, message=f"本群还没有{title}喵！"
            )
            return True
        batches = []
        current_batch = []
        current_batch_length = 0
        for i, text in enumerate(texts, 1):
            text_with_number = f"{i}. {text}"
            if len(text_with_number) > MAX_TEXT_LENGTH_PER_NODE:
                if current_batch:
                    batches.append(current_batch)
                batches.append([text_with_number])
                current_batch = []
                current_batch_length = 0
                continue
            if (
                len(current_batch) >= MAX_NODES_PER_FORWARD
                or current_batch_length + len(text_with_number)
                > MAX_TEXT_LENGTH_PER_NODE
            ):
                batches.append(current_batch)
                current_batch = []
                current_batch_length = 0
            current_batch.append(text_with_number)
            current_batch_length += len(text_with_number)
        if current_batch:
            batches.append(current_batch)

        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, 1):
            messages = []
            batch_title = f"📋 {title}" + (
                f" - 第{batch_index}批/共{total_batches}批" if total_batches > 1 else ""
            )
            messages.append(("投稿内容", "text", batch_title))
            for text_item in batch:
                messages.append(("投稿内容", "text", text_item))

            forward_nodes = await create_forward_message(bot, group_id, messages)
            await send_forward_msg(bot, group_id=group_id, items=forward_nodes)
            if batch_index < total_batches:
                await asyncio.sleep(1)
        return True
    except Exception as e:
        logger.error(f"发送文本合并转发消息失败: {e}")
        return False


async def send_image_forward_message(
    bot: Bot, group_id: int, image_filenames: List[str], title: str = "图片投稿"
) -> bool:
    try:
        if not image_filenames:
            await bot.send_group_msg(
                group_id=group_id, message=f"本群还没有{title}喵！"
            )
            return True
        batches = [
            image_filenames[i : i + MAX_IMAGES_PER_BATCH]
            for i in range(0, len(image_filenames), MAX_IMAGES_PER_BATCH)
        ]

        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, 1):
            messages = []
            batch_title = f"🖼️ {title}" + (
                f" - 第{batch_index}批/共{total_batches}批" if total_batches > 1 else ""
            )
            messages.append(("投稿内容", "text", batch_title))

            for filename in batch:
                image_path = get_group_image_dir(group_id) / filename
                if image_path.exists():
                    messages.append(("投稿内容", "image", image_segment(image_path)))
                else:
                    logger.warning(f"图片文件不存在: {image_path}")

            forward_nodes = await create_forward_message(bot, group_id, messages)
            await send_forward_msg(bot, group_id=group_id, items=forward_nodes)
            if batch_index < total_batches:
                await asyncio.sleep(1)
        return True
    except Exception as e:
        logger.error(f"发送图片合并转发消息失败: {e}")
        return False


@view_all_contributions.handle()
async def handle_view_all_contributions(bot: Bot, event: GroupMessageEvent):
    try:
        group_id = event.group_id
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_contributions.finish("数据加载失败，无法查看投稿喵！")
        texts = data_manager.group_texts.get(group_id, [])
        images = data_manager.group_images.get(group_id, [])
        if not texts and not images:
            await view_all_contributions.finish("本群还没有任何投稿内容喵！")
        if texts:
            if not await send_text_forward_message(
                bot, group_id, texts, "所有投稿文本"
            ):
                return
            await asyncio.sleep(2)
        if images:
            if not await send_image_forward_message(
                bot, group_id, images, "所有投稿图片"
            ):
                return
        logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有投稿")
    except Exception as e:
        logger.error(f"查看所有投稿时出错: {e}")
        await view_all_contributions.finish("查看投稿失败，请稍后重试喵！")


@view_all_texts.handle()
async def handle_view_all_texts(bot: Bot, event: GroupMessageEvent):
    try:
        group_id = event.group_id
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_texts.finish("数据加载失败，无法查看文本喵！")
        texts = data_manager.group_texts.get(group_id, [])
        if await send_text_forward_message(bot, group_id, texts, "所有文本投稿"):
            logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有文本投稿")
    except Exception as e:
        logger.error(f"查看所有文本时出错: {e}")
        await view_all_texts.finish("查看文本失败，请稍后重试喵！")


@view_all_images.handle()
async def handle_view_all_images(bot: Bot, event: GroupMessageEvent):
    try:
        group_id = event.group_id
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_images.finish("数据加载失败，无法查看图片喵！")
        images = data_manager.group_images.get(group_id, [])
        if await send_image_forward_message(bot, group_id, images, "所有图片投稿"):
            logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有图片投稿")
    except Exception as e:
        logger.error(f"查看所有图片时出错: {e}")
        await view_all_images.finish("查看图片失败，请稍后重试喵！")
