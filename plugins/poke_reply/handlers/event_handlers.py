# handlers/event_handlers.py
from nonebot import on_notice, on_command, on_message, logger
from nonebot.adapters.onebot.v11 import PokeNotifyEvent, Message, MessageEvent, GroupMessageEvent, MessageSegment, Bot
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from typing import Tuple
import random
import os
import hashlib

from ..config import CONTRIBUTE_COMMAND_PRIORITY, MAX_TEXT_LENGTH, get_group_image_dir
from ..config import TEXT_TO_IMAGE_LENGTH_THRESHOLD, is_text_to_image_enabled
from ..core.data_manager import data_manager
from ..core.similarity_check import similarity_checker
from ..services.text_to_image import convert_text_to_image
from ..services.text_image_cache import text_image_cache
from ..managers.cache_manager import message_cache
from ..managers.poke_cd_manager import poke_cd_manager
from ..utils.common import download_image, get_group_id, extract_image_data, ensure_at_me
from ...plugin_manager import *

# 注册事件处理器
poke = on_notice()
contribute = on_command("投稿", rule=ensure_at_me() & to_me(), priority=CONTRIBUTE_COMMAND_PRIORITY, block=True)

# 创建单独的处理器来处理提及消息
mention_handler = on_message(rule=to_me(), priority=15, block=False)

def cache_message_direct(group_id: int, message_id: int, content: str,
                         message_type: str = "text", image_hash: str = ""):
    """直接缓存消息（不依赖消息结果）"""
    try:
        message_cache.add_message(
            group_id=group_id,
            message_id=message_id,
            content=content,
            message_type=message_type,
            image_hash=image_hash
        )
        logger.info(f"直接缓存消息: 群组={group_id}, 消息ID={message_id}, 类型={message_type}")
    except Exception as e:
        logger.error(f"直接缓存消息失败: {e}")


@poke.handle()
async def handle_poke(bot: Bot, event: PokeNotifyEvent):
    """
    处理戳一戳事件 - 集成CD检查功能
    """

    # 确保事件类型为戳一戳，并且目标是机器人自己
    if event.notice_type != "notify" or event.sub_type != "poke" or event.target_id != event.self_id:
        return

    group_id = get_group_id(event)
    if group_id == 0:
        return  # 私聊戳一戳不处理

    if not is_feature_enabled("poke_reply", "poke", str(group_id)):
        await poke.finish("本群未开启戳一戳回复！")
        return  # 直接返回，不执行后续逻辑

    # CD检查（只对非superuser用户生效）
    user_id = event.user_id
    is_superuser = str(user_id) in bot.config.superusers

    if not is_superuser:
        in_cd, remaining_time = poke_cd_manager.check_cd(group_id, user_id)
        if in_cd:
            logger.info(f"群 {group_id} 用户 {user_id} 戳一戳CD中，剩余 {remaining_time:.1f}秒")
            return  # 在CD中，直接返回不处理

    try:
        # 确保数据已加载
        if not data_manager.ensure_group_data_loaded(group_id):
            logger.error(f"群 {group_id} 数据加载失败")
            await bot.send_group_msg(group_id=group_id, message="数据加载失败，请联系管理员喵！")
            return

        # 获取文本和图片的数量作为权重
        text_count, image_count = data_manager.get_content_weights(group_id)
        total_count = text_count + image_count

        if total_count == 0:
            # 没有任何内容
            await bot.send_group_msg(group_id=group_id, message="这个群还没有投稿内容喵，快来投稿吧！")
            return

        # 根据权重随机选择内容类型
        choice = random.choices(
            ['text', 'image'],
            weights=[text_count, image_count],
            k=1
        )[0]

        if choice == 'text':
            # 发送随机文本
            selected_text = data_manager.get_random_text(group_id)

            # 检查是否启用文本转图片且文本超过阈值
            if (is_text_to_image_enabled(group_id) and
                    len(selected_text) > TEXT_TO_IMAGE_LENGTH_THRESHOLD):

                logger.info(f"群 {group_id} 戳一戳检测到长文本，长度: {len(selected_text)}，开始转换为图片")

                # 转换为图片
                success, image_data = await convert_text_to_image(selected_text, group_id)

                if success and image_data:
                    # 计算图片的MD5哈希值
                    image_hash = hashlib.md5(image_data).hexdigest()

                    # 发送图片消息
                    result = await bot.send_group_msg(
                        group_id=group_id,
                        message=Message(MessageSegment.image(image_data))
                    )

                    # 缓存文本和图片信息，使用图片哈希作为键
                    text_image_cache.add_cache_by_image_hash(
                        image_hash=image_hash,
                        group_id=group_id,
                        original_text=selected_text
                    )

                    # 直接缓存消息
                    cache_message_direct(
                        group_id=group_id,
                        message_id=result['message_id'],
                        content=selected_text,
                        message_type="text_image",
                        image_hash=image_hash
                    )

                    logger.info(f"已缓存长文本转换的图片消息，消息ID: {result['message_id']}")

                else:
                    logger.error(f"群 {group_id} 戳一戳文本转图片失败，回退到原始文本")
                    # 转换失败，回退到原始文本
                    result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                    # 缓存普通文本消息
                    cache_message_direct(
                        group_id=group_id,
                        message_id=result['message_id'],
                        content=selected_text,
                        message_type="text"
                    )
            else:
                # 文本未超过阈值或未启用转图片功能，直接发送文本
                result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                # 缓存普通文本消息
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=selected_text,
                    message_type="text"
                )
        else:
            # 发送随机图片
            image_path = data_manager.get_random_image_path(group_id)
            if image_path and os.path.exists(image_path):
                result = await bot.send_group_msg(
                    group_id=group_id,
                    message=Message(MessageSegment.image(f"file:///{image_path}"))
                )

                # 缓存图片消息
                filename = os.path.basename(image_path)
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=filename,
                    message_type="image"
                )
            else:
                # 如果图片加载失败，回退到文本
                logger.warning(f"群 {group_id} 图片加载失败，回退到文本")
                selected_text = data_manager.get_random_text(group_id)
                result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                # 缓存普通文本消息
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=selected_text,
                    message_type="text"
                )

    except Exception as e:
        logger.error(f"处理戳一戳事件时发生错误: {e}")
        await bot.send_group_msg(group_id=group_id, message="发生了一些错误，请稍后重试喵~")


@contribute.handle()
async def handle_contribute(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """
    处理投稿命令 - 现在支持图片投稿
    """
    group_id = get_group_id(event)
    if group_id == 0:
        await contribute.finish("请在群聊中使用投稿功能喵！")
        return

    # 检查功能是否启用
    if not is_feature_enabled("poke_reply", "contribute", str(event.group_id)):
        await contribute.finish("本群未开启投稿功能！")
        return

    # 检查消息中是否包含图片
    has_images, images = extract_image_data(event.get_message())

    if has_images:
        # 处理图片投稿
        await handle_image_contribute(bot, group_id, images)
    else:
        # 处理文本投稿
        await handle_text_contribute(bot, group_id, event, args)


async def handle_text_contribute(bot: Bot, group_id: int, event: MessageEvent, args: Message):
    """处理文本投稿"""
    # 获取投稿内容
    args_text = args.extract_plain_text().strip()
    if not args_text:
        await contribute.finish("请提供要投稿的内容，格式：投稿 你的文本")
        return

    # 检查内容长度
    if len(args_text) > MAX_TEXT_LENGTH:
        await contribute.finish(f"文本太长了，请控制在{MAX_TEXT_LENGTH}字以内喵！")
        return

    # 确保数据已加载
    if not data_manager.ensure_group_data_loaded(group_id):
        await contribute.finish("数据加载失败，无法投稿喵！")
        return

    if not data_manager.is_text_list_valid(group_id):
        await contribute.finish("数据格式错误，无法投稿喵！")
        return

    # 查重检查 - 使用群组独立查重方法
    if similarity_checker.is_similar_to_group(group_id, args_text):
        await contribute.finish("投稿失败，本群已经有类似的话了喵！")
        return

    # 添加到文本列表
    if data_manager.add_text(group_id, args_text):
        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)

        # 发送成功消息并缓存
        result = await bot.send_group_msg(
            group_id=group_id,
            message=f"文本投稿成功！当前群共有{text_count}条文本和{image_count}张图片喵"
        )

        # 缓存投稿成功消息（方便管理）
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=args_text,
            message_type="contribute_text"
        )
    else:
        await contribute.finish("投稿失败，请稍后重试喵")


async def handle_image_contribute(bot: Bot, group_id: int, images: list):
    """处理图片投稿"""
    # 确保数据已加载
    if not data_manager.ensure_group_data_loaded(group_id):
        await contribute.finish("数据加载失败，无法投稿喵！")
        return

    success_count = 0
    total_count = len(images)
    saved_filenames = []

    for img_type, img_data, segment in images:
        if img_type == "image":
            # 下载并保存图片
            success, image_bytes, extension = await download_image(img_data)
            if success:
                success_add, filename = data_manager.add_image(group_id, image_bytes, extension)
                if success_add:
                    success_count += 1
                    saved_filenames.append(filename)
                    logger.info(f"成功保存图片: {filename}")
                else:
                    logger.error(f"保存图片到列表失败: {img_data}")
            else:
                logger.error(f"下载图片失败: {img_data}")
        elif img_type == "face":
            # 对于表情，我们可以保存为特殊格式或忽略
            # 这里暂时忽略表情投稿，或者可以保存表情ID
            await contribute.finish("暂不支持表情符号投稿喵！")
            return

    if success_count > 0:
        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)

        # 发送成功消息并缓存
        result = await bot.send_group_msg(
            group_id=group_id,
            message=f"图片投稿成功！成功上传{success_count}/{total_count}张图片。当前群共有{text_count}条文本和{image_count}张图片喵"
        )

        # 缓存投稿成功消息（包含所有保存的文件名）
        content_info = f"图片投稿: {', '.join(saved_filenames)}"
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=content_info,
            message_type="contribute_image"
        )
    else:
        await contribute.finish("图片投稿失败，请稍后重试喵")


# 修复：处理提及消息的单独处理器
@mention_handler.handle()
async def handle_mention_message(bot: Bot, event: GroupMessageEvent):
    """处理提及机器人的消息，进行缓存"""
    try:
        group_id = event.group_id
        message_id = event.message_id
        message_text = event.get_plaintext().strip()

        # 只缓存有实际内容的文本消息
        if message_text and len(message_text) > 0:
            # 检查是否是命令消息，避免缓存命令
            is_command = any(cmd in message_text for cmd in [
                "投稿", "申请删除", "查看文本数", "查看投稿统计",
                "启用文本转图片", "禁用文本转图片", "文本转图片状态",
                "设置文本阈值", "处理删除", "查看删除申请", "转文字",
                "启用戳一戳CD", "禁用戳一戳CD", "戳一戳CD状态", "设置戳一戳CD"  # 新增：CD相关命令
            ])

            if not is_command:
                cache_message_direct(
                    group_id=group_id,
                    message_id=message_id,
                    content=message_text,
                    message_type="mention_text"
                )
                logger.debug(f"已缓存提及消息: 群组={group_id}, 消息ID={message_id}")

    except Exception as e:
        logger.error(f"处理提及消息缓存时出错: {e}")