# handlers/view_contributions.py
from nonebot import on_command, logger, get_bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, Bot
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException
from typing import List, Tuple
import asyncio

from ..core.data_manager import data_manager
from ..config import get_group_image_dir
from ..utils.common import ensure_at_me, create_forward_message, image_to_base64

# 注册命令处理器
view_all_contributions = on_command("查看所有投稿", rule=ensure_at_me() & to_me(), priority=5, block=True)
view_all_texts = on_command("查看所有文本", rule=ensure_at_me() & to_me(), priority=5, block=True)
view_all_images = on_command("查看所有图片", rule=ensure_at_me() & to_me(), priority=5, block=True)

# 配置常量
MAX_NODES_PER_FORWARD = 30  # 每个合并转发消息最多包含的节点数
MAX_TEXT_LENGTH_PER_NODE = 20000  # 每个节点最多包含的文本长度
MAX_IMAGES_PER_BATCH = 15  # 每批最多包含的图片数


async def send_text_forward_message(bot: Bot, group_id: int, texts: List[str], title: str = "文本投稿") -> bool:
    """
    发送文本合并转发消息（分批次发送）

    Args:
        bot: 机器人实例
        group_id: 群组ID
        texts: 文本列表
        title: 消息标题

    Returns:
        bool: 是否发送成功
    """
    try:
        if not texts:
            await bot.send_group_msg(group_id=group_id, message=f"本群还没有{title}喵！")
            return True

        # 将文本分批处理
        batches = []
        current_batch = []
        current_batch_length = 0

        for i, text in enumerate(texts, 1):
            text_with_number = f"{i}. {text}"

            # 如果单条文本过长，需要单独处理
            if len(text_with_number) > MAX_TEXT_LENGTH_PER_NODE:
                # 当前批次如果有内容，先保存
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_batch_length = 0

                # 长文本单独作为一个批次
                batches.append([text_with_number])
                continue

            # 检查是否应该开始新批次
            if (len(current_batch) >= MAX_NODES_PER_FORWARD or
                    current_batch_length + len(text_with_number) > MAX_TEXT_LENGTH_PER_NODE):
                batches.append(current_batch)
                current_batch = []
                current_batch_length = 0

            current_batch.append(text_with_number)
            current_batch_length += len(text_with_number)

        # 添加最后一个批次
        if current_batch:
            batches.append(current_batch)

        # 发送所有批次
        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, 1):
            messages = []

            # 添加批次标题
            if total_batches > 1:
                batch_title = f"📋 {title} - 第{batch_index}批/共{total_batches}批"
            else:
                batch_title = f"📋 {title}"

            messages.append(("投稿内容", "text", batch_title))

            # 添加本批次的文本内容
            for text_item in batch:
                messages.append(("投稿内容", "text", text_item))

            # 创建并发送合并转发消息
            forward_nodes = await create_forward_message(bot, group_id, messages)
            await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)

            # 批次间延迟，避免发送过快
            if batch_index < total_batches:
                await asyncio.sleep(1)

        return True

    except Exception as e:
        logger.error(f"发送文本合并转发消息失败: {e}")
        # 尝试回退到普通消息发送
        try:
            await bot.send_group_msg(
                group_id=group_id,
                message=f"{title}内容过多，发送失败。建议分批查看或联系管理员喵！"
            )
        except:
            pass
        return False


async def send_image_forward_message(bot: Bot, group_id: int, image_filenames: List[str],
                                     title: str = "图片投稿") -> bool:
    """
    发送图片合并转发消息（图片直接嵌入合并转发消息，不显示文件名）

    Args:
        bot: 机器人实例
        group_id: 群组ID
        image_filenames: 图片文件名列表
        title: 消息标题

    Returns:
        bool: 是否发送成功
    """
    try:
        if not image_filenames:
            await bot.send_group_msg(group_id=group_id, message=f"本群还没有{title}喵！")
            return True

        # 将图片分批处理
        batches = []
        current_batch = []

        for i, filename in enumerate(image_filenames, 1):
            current_batch.append((i, filename))

            # 如果达到每批最大图片数，开始新批次
            if len(current_batch) >= MAX_IMAGES_PER_BATCH:
                batches.append(current_batch)
                current_batch = []

        # 添加最后一个批次
        if current_batch:
            batches.append(current_batch)

        # 发送所有批次
        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, 1):
            messages = []

            # 添加批次标题（简化版，不显示本批图片数量）
            if total_batches > 1:
                batch_title = f"🖼️ {title} - 第{batch_index}批/共{total_batches}批"
            else:
                batch_title = f"🖼️ {title}"

            messages.append(("投稿内容", "text", batch_title))

            # 处理本批次的图片 - 只发送图片，不发送文件名
            for global_index, filename in batch:
                image_path = get_group_image_dir(group_id) / filename

                if image_path.exists():
                    # 将图片转换为base64
                    success, base64_data = image_to_base64(image_path)

                    if success:
                        # 只添加图片节点，不添加描述文本
                        messages.append(("投稿内容", "image", base64_data))
                    else:
                        # 图片转换失败，记录日志但不发送错误信息
                        logger.warning(f"图片转换失败: {filename}, 错误: {base64_data}")
                else:
                    # 图片文件不存在，记录日志但不发送错误信息
                    logger.warning(f"图片文件不存在: {image_path}")

            # 创建并发送合并转发消息
            forward_nodes = await create_forward_message(bot, group_id, messages)
            await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)

            # 批次间延迟，避免发送过快
            if batch_index < total_batches:
                await asyncio.sleep(1)

        return True

    except Exception as e:
        logger.error(f"发送图片合并转发消息失败: {e}")
        # 尝试回退到普通消息发送
        try:
            await bot.send_group_msg(
                group_id=group_id,
                message=f"{title}发送失败，请稍后重试或联系管理员喵！"
            )
        except:
            pass
        return False


@view_all_contributions.handle()
async def handle_view_all_contributions(bot: Bot, event: GroupMessageEvent):
    """查看本群所有投稿（文本+图片）"""
    try:
        group_id = event.group_id

        # 确保数据已加载
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_contributions.finish("数据加载失败，无法查看投稿喵！")
            return

        # 获取文本和图片数据
        texts = data_manager.group_texts.get(group_id, [])
        images = data_manager.group_images.get(group_id, [])

        if not texts and not images:
            await view_all_contributions.finish("本群还没有任何投稿内容喵！")
            return

        # 先发送文本内容
        if texts:
            success = await send_text_forward_message(bot, group_id, texts, "所有投稿文本")
            if not success:
                return
            await asyncio.sleep(2)  # 文本和图片之间间隔一下

        # 再发送图片内容
        if images:
            success = await send_image_forward_message(bot, group_id, images, "所有投稿图片")
            if not success:
                return

        logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有投稿")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"查看所有投稿时出错: {e}")
        await view_all_contributions.finish("查看投稿失败，请稍后重试喵！")


@view_all_texts.handle()
async def handle_view_all_texts(bot: Bot, event: GroupMessageEvent):
    """查看本群所有文本投稿"""
    try:
        group_id = event.group_id

        # 确保数据已加载
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_texts.finish("数据加载失败，无法查看文本喵！")
            return

        # 获取文本数据
        texts = data_manager.group_texts.get(group_id, [])

        # 发送文本合并转发消息
        success = await send_text_forward_message(bot, group_id, texts, "所有文本投稿")

        if success:
            logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有文本投稿")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"查看所有文本时出错: {e}")
        await view_all_texts.finish("查看文本失败，请稍后重试喵！")


@view_all_images.handle()
async def handle_view_all_images(bot: Bot, event: GroupMessageEvent):
    """查看本群所有图片投稿"""
    try:
        group_id = event.group_id

        # 确保数据已加载
        if not data_manager.ensure_group_data_loaded(group_id):
            await view_all_images.finish("数据加载失败，无法查看图片喵！")
            return

        # 获取图片数据
        images = data_manager.group_images.get(group_id, [])

        # 发送图片合并转发消息
        success = await send_image_forward_message(bot, group_id, images, "所有图片投稿")

        if success:
            logger.info(f"用户 {event.user_id} 查看了群 {group_id} 的所有图片投稿")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"查看所有图片时出错: {e}")
        await view_all_images.finish("查看图片失败，请稍后重试喵！")