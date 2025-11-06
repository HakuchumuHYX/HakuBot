# poke_reply/event_handlers.py
from nonebot import on_notice, on_command, on_message, logger, get_bot
from nonebot.adapters.onebot.v11 import (
    PokeNotifyEvent, Message, MessageEvent, GroupMessageEvent, MessageSegment, Bot
)
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from typing import Tuple
import random
import os
import hashlib

# vvvvvv 【修改：导入路径】 vvvvvv
from .config import (
    CONTRIBUTE_COMMAND_PRIORITY, MAX_TEXT_LENGTH, get_group_image_dir,
    TEXT_TO_IMAGE_LENGTH_THRESHOLD, is_text_to_image_enabled
)
from .data_manager import data_manager
from .similarity_check import similarity_checker
from .text_to_image import convert_text_to_image
from .managers import message_cache, text_image_cache
from .common import (
    download_image, get_group_id, extract_image_data,
    ensure_at_me, download_and_hash_image, create_forward_message
)
# ^^^^^^ 【修改：导入路径】 ^^^^^^


# vvvvvv 【修改：导入路径 - 外部插件】 vvvvvv
from ..plugin_manager.enable import *
from ..plugin_manager.cd_manager import check_cd, update_cd

# ^^^^^^ 【修改：导入路径 - 外部插件】 ^^^^^^


# 注册事件处理器
poke = on_notice()
contribute = on_command("投稿", rule=ensure_at_me() & to_me(), priority=CONTRIBUTE_COMMAND_PRIORITY, block=True)
mention_handler = on_message(rule=to_me(), priority=15, block=False)
convert_to_text = on_message(rule=to_me(), priority=10, block=True)  # “转文字”处理器移入


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
    except Exception as e:
        logger.error(f"直接缓存消息失败: {e}")


@poke.handle()
async def handle_poke(bot: Bot, event: PokeNotifyEvent):
    """
    处理戳一戳事件 - 集成【外部】CD检查功能
    """
    if event.notice_type != "notify" or event.sub_type != "poke" or event.target_id != event.self_id:
        return

    group_id = get_group_id(event)
    if group_id == 0:
        return

    # 检查插件功能开关
    if not is_feature_enabled("poke_reply", "poke", str(group_id)):
        return  # 静默返回

    # 接入外部CD管理
    PLUGIN_ID = "poke_reply:poke"
    user_id = event.user_id
    is_superuser = str(user_id) in bot.config.superusers

    if not is_superuser:
        # 1. 检查CD
        remaining_time = check_cd(PLUGIN_ID, str(group_id), str(user_id))
        if remaining_time > 0:
            logger.info(f"群 {group_id} 用户 {user_id} 戳一戳CD中，剩余 {remaining_time}秒")
            return  # 在CD中，直接返回不处理

        # 2. 更新CD (CD检查通过后，立即更新CD时间)
        update_cd(PLUGIN_ID, str(group_id), str(user_id))

    try:
        if not data_manager.ensure_group_data_loaded(group_id):
            await bot.send_group_msg(group_id=group_id, message="数据加载失败，请联系管理员喵！")
            return

        text_count, image_count = data_manager.get_content_weights(group_id)
        total_count = text_count + image_count

        if total_count == 0:
            await bot.send_group_msg(group_id=group_id, message="这个群还没有投稿内容喵，快来投稿吧！")
            return

        choice = random.choices(['text', 'image'], weights=[text_count, image_count], k=1)[0]

        if choice == 'text':
            selected_text = data_manager.get_random_text(group_id)
            if (is_text_to_image_enabled(group_id) and
                    len(selected_text) > TEXT_TO_IMAGE_LENGTH_THRESHOLD):

                success, image_data = await convert_text_to_image(selected_text, group_id)
                if success and image_data:
                    image_hash = hashlib.md5(image_data).hexdigest()
                    result = await bot.send_group_msg(
                        group_id=group_id,
                        message=Message(MessageSegment.image(image_data))
                    )
                    text_image_cache.add_cache_by_image_hash(
                        image_hash=image_hash,
                        group_id=group_id,
                        original_text=selected_text
                    )
                    cache_message_direct(
                        group_id=group_id,
                        message_id=result['message_id'],
                        content=selected_text,
                        message_type="text_image",
                        image_hash=image_hash
                    )
                else:
                    result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                    cache_message_direct(
                        group_id=group_id,
                        message_id=result['message_id'],
                        content=selected_text,
                        message_type="text"
                    )
            else:
                result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=selected_text,
                    message_type="text"
                )
        else:
            image_path = data_manager.get_random_image_path(group_id)
            if image_path and os.path.exists(image_path):
                result = await bot.send_group_msg(
                    group_id=group_id,
                    message=Message(MessageSegment.image(f"file:///{image_path}"))
                )
                filename = os.path.basename(image_path)
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=filename,
                    message_type="image"
                )
            else:
                logger.warning(f"群 {group_id} 图片加载失败，回退到文本")
                selected_text = data_manager.get_random_text(group_id)
                result = await bot.send_group_msg(group_id=group_id, message=selected_text)
                cache_message_direct(
                    group_id=group_id,
                    message_id=result['message_id'],
                    content=selected_text,
                    message_type="text"
                )
    except Exception as e:
        logger.error(f"处理戳一戳事件时发生错误: {e}")


@contribute.handle()
async def handle_contribute(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = get_group_id(event)
    if group_id == 0:
        await contribute.finish("请在群聊中使用投稿功能喵！")

    # 检查功能开关
    if not is_feature_enabled("poke_reply", "contribute", str(group_id)):
        await contribute.finish("本群未开启投稿功能！")

    # vvvvvv 【新增：为“投稿”功能添加CD】 vvvvvv
    PLUGIN_ID_CONTRIB = "poke_reply:contribute"
    user_id = event.user_id
    is_superuser = str(user_id) in bot.config.superusers

    if not is_superuser:
        remaining_time = check_cd(PLUGIN_ID_CONTRIB, str(group_id), str(user_id))
        if remaining_time > 0:
            await contribute.finish(f"投稿功能冷却中，请等待 {remaining_time} 秒喵！")
    # ^^^^^^ 【新增：为“投稿”功能添加CD】 ^^^^^^

    has_images, images = extract_image_data(event.get_message())
    if has_images:
        # vvvvvv 【修改：传递CD信息】 vvvvvv
        await handle_image_contribute(bot, group_id, images, PLUGIN_ID_CONTRIB, str(user_id), is_superuser)
        # ^^^^^^ 【修改：传递CD信息】 ^^^^^^
    else:
        # vvvvvv 【修改：传递CD信息】 vvvvvv
        await handle_text_contribute(bot, group_id, event, args, PLUGIN_ID_CONTRIB, str(user_id), is_superuser)
        # ^^^^^^ 【修改：传递CD信息】 ^^^^^^


async def handle_text_contribute(bot: Bot, group_id: int, event: MessageEvent, args: Message,
                                 plugin_id: str, user_id: str, is_superuser: bool):  # vvvvvv 【修改：添加CD参数】 vvvvvv
    args_text = args.extract_plain_text().strip()
    if not args_text:
        await contribute.finish("请提供要投稿的内容，格式：投稿 你的文本")
    if len(args_text) > MAX_TEXT_LENGTH:
        await contribute.finish(f"文本太长了，请控制在{MAX_TEXT_LENGTH}字以内喵！")
    if not data_manager.ensure_group_data_loaded(group_id):
        await contribute.finish("数据加载失败，无法投稿喵！")
    if not data_manager.is_text_list_valid(group_id):
        await contribute.finish("数据格式错误，无法投稿喵！")
    if similarity_checker.is_similar_to_group(group_id, args_text):
        await contribute.finish("投稿失败，本群已经有类似的话了喵！")

    if data_manager.add_text(group_id, args_text):
        # vvvvvv 【新增：投稿成功后更新CD】 vvvvvv
        if not is_superuser:
            update_cd(plugin_id, str(group_id), user_id)
        # ^^^^^^ 【新增：投稿成功后更新CD】 ^^^^^^

        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)
        result = await bot.send_group_msg(
            group_id=group_id,
            message=f"文本投稿成功！当前群共有{text_count}条文本和{image_count}张图片喵"
        )
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=args_text,
            message_type="contribute_text"
        )
    else:
        await contribute.finish("投稿失败，请稍后重试喵")


async def handle_image_contribute(bot: Bot, group_id: int, images: list,
                                  plugin_id: str, user_id: str, is_superuser: bool):  # vvvvvv 【修改：添加CD参数】 vvvvvv
    if not data_manager.ensure_group_data_loaded(group_id):
        await contribute.finish("数据加载失败，无法投稿喵！")
    success_count = 0
    total_count = len(images)
    saved_filenames = []
    for img_type, img_data, segment in images:
        if img_type == "image":
            success, image_bytes, extension = await download_image(img_data)
            if success:
                success_add, filename = data_manager.add_image(group_id, image_bytes, extension)
                if success_add:
                    success_count += 1
                    saved_filenames.append(filename)
            else:
                logger.error(f"下载图片失败: {img_data}")
        elif img_type == "face":
            await contribute.finish("暂不支持表情符号投稿喵！")
            return

    if success_count > 0:
        # vvvvvv 【新增：投稿成功后更新CD】 vvvvvv
        if not is_superuser:
            update_cd(plugin_id, str(group_id), user_id)
        # ^^^^^^ 【新增：投稿成功后更新CD】 ^^^^^^

        text_count = data_manager.get_text_count(group_id)
        image_count = data_manager.get_image_count(group_id)
        result = await bot.send_group_msg(
            group_id=group_id,
            message=f"图片投稿成功！成功上传{success_count}/{total_count}张图片。当前群共有{text_count}条文本和{image_count}张图片喵"
        )
        content_info = f"图片投稿: {', '.join(saved_filenames)}"
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=content_info,
            message_type="contribute_image"
        )
    else:
        await contribute.finish("图片投稿失败，请稍后重试喵")


@mention_handler.handle()
async def handle_mention_message(bot: Bot, event: GroupMessageEvent):
    try:
        group_id = event.group_id
        message_id = event.message_id
        message_text = event.get_plaintext().strip()
        if message_text and len(message_text) > 0:
            is_command = any(cmd in message_text for cmd in [
                "投稿", "申请删除", "查看文本数", "查看投稿统计",
                "启用文本转图片", "禁用文本转图片", "文本转图片状态",
                "设置文本阈值", "处理删除", "查看删除申请", "转文字",
                # (已移除CD相关命令)
            ])
            if not is_command:
                cache_message_direct(
                    group_id=group_id,
                    message_id=message_id,
                    content=message_text,
                    message_type="mention_text"
                )
    except Exception as e:
        logger.error(f"处理提及消息缓存时出错: {e}")


@convert_to_text.handle()
async def handle_convert_to_text(bot: Bot, event: GroupMessageEvent):
    """处理'转文字'回复"""
    try:
        message_text = event.get_plaintext().strip()
        if message_text != "转文字":
            return  # 不是转文字命令，交给其他处理器

        if not hasattr(event, 'reply') or event.reply is None:
            await convert_to_text.finish("请回复要转换的图片消息并说'转文字'喵！")

        replied_message = event.reply
        group_id = event.group_id
        image_url = None
        for segment in replied_message.message:
            if segment.type == "image":
                image_url = segment.data.get("url", "")
                break

        if not image_url:
            await convert_to_text.finish("请回复一张由长文本转换的图片消息喵！")

        success, image_hash = await download_and_hash_image(image_url)
        if not success:
            await convert_to_text.finish("下载图片失败，无法计算哈希值喵！")

        cache_record = text_image_cache.get_cache_by_image_hash(image_hash, group_id)
        if not cache_record:
            await convert_to_text.finish("未找到对应的文本缓存，可能已过期或不是由长文本转换的图片喵！")

        original_text = cache_record.get("original_text", "")
        if not original_text:
            await convert_to_text.finish("缓存文本为空，无法转换喵！")

        # 使用 common.py 中的 create_forward_message
        messages = [("转文字结果", "text", original_text)]
        forward_nodes = await create_forward_message(bot, group_id, messages)

        await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)
        text_image_cache.remove_cache_by_image_hash(image_hash, group_id)

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理转文字命令时出错: {e}")
        await convert_to_text.finish("转换失败，请稍后重试喵！")
