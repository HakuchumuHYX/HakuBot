from typing import Optional
from nonebot import on_command, on_message, logger
from nonebot.adapters.onebot.v11 import (
    Message, GroupMessageEvent, MessageEvent, Bot
)
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException

from ..config import CONTRIBUTE_COMMAND_PRIORITY
from ..models.cache import message_cache, text_image_cache
from ..utils.common import (
    get_group_id, extract_image_data, ensure_at_me,
    create_forward_message
)
from ..utils.network import download_and_hash_image
from ..services.contribute import handle_text_contribution, handle_image_contribution
from plugins.plugin_manager.enable import is_feature_enabled
from plugins.plugin_manager.cd_manager import check_cd, update_cd

# --- 注册匹配器 ---
contribute = on_command("投稿", rule=ensure_at_me() & to_me(), priority=CONTRIBUTE_COMMAND_PRIORITY, block=True)
mention_handler = on_message(rule=to_me(), priority=15, block=False)
convert_to_text = on_message(rule=to_me(), priority=10, block=True)

# --- 辅助函数：缓存 ---
def cache_message_direct(group_id: int, message_id: int, content: str,
                         message_type: str = "text", image_hash: str = ""):
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

# --- 投稿处理器 ---

@contribute.handle()
async def handle_contribute(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = get_group_id(event)
    if group_id == 0:
        await contribute.finish("请在群聊中使用投稿功能喵！")

    user_id = str(event.user_id)
    if not is_feature_enabled("poke_reply", "contribute", str(group_id), user_id):
        await contribute.finish("本群未开启投稿功能！")

    # CD检查
    PLUGIN_ID_CONTRIB = "poke_reply:contribute"
    is_superuser = str(user_id) in bot.config.superusers

    if not is_superuser:
        remaining_time = check_cd(PLUGIN_ID_CONTRIB, str(group_id), str(user_id))
        if remaining_time > 0:
            await contribute.finish(f"投稿功能冷却中，请等待 {remaining_time} 秒喵！")

    replied_message = event.reply
    target_message = event.get_message()

    if replied_message:
        target_message = replied_message.message
        has_images, images = extract_image_data(target_message)
        if has_images:
            await process_image_contribution(bot, group_id, images, PLUGIN_ID_CONTRIB, user_id, is_superuser)
        else:
            text_to_add = target_message.extract_plain_text().strip()
            if not text_to_add:
                await contribute.finish("回复的消息没有文本内容喵！")
            await process_text_contribution(bot, group_id, text_to_add, PLUGIN_ID_CONTRIB, user_id, is_superuser)
    else:
        has_images, images = extract_image_data(target_message)
        if has_images:
            await process_image_contribution(bot, group_id, images, PLUGIN_ID_CONTRIB, user_id, is_superuser)
        else:
            args_text = args.extract_plain_text().strip()
            await process_text_contribution(bot, group_id, args_text, PLUGIN_ID_CONTRIB, user_id, is_superuser)

async def process_text_contribution(bot: Bot, group_id: int, text: str, plugin_id: str, user_id: str, is_superuser: bool):
    success, message = await handle_text_contribution(group_id, text)
    if success:
        if not is_superuser:
            update_cd(plugin_id, str(group_id), user_id)
        result = await bot.send_group_msg(group_id=group_id, message=message)
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=text,
            message_type="contribute_text"
        )
    else:
        await contribute.finish(message)

async def process_image_contribution(bot: Bot, group_id: int, images: list, plugin_id: str, user_id: str, is_superuser: bool):
    success, message, saved_filenames = await handle_image_contribution(group_id, images)
    if success:
        if not is_superuser:
            update_cd(plugin_id, str(group_id), user_id)
        result = await bot.send_group_msg(group_id=group_id, message=message)
        content_info = f"图片投稿: {', '.join(saved_filenames)}"
        cache_message_direct(
            group_id=group_id,
            message_id=result['message_id'],
            content=content_info,
            message_type="contribute_image"
        )
    else:
        await contribute.finish(message)

# --- 提及缓存 ---

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
                "设置文本阈值", "处理删除", "查看删除申请", "转文字"
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

# --- 转文字 ---

@convert_to_text.handle()
async def handle_convert_to_text(bot: Bot, event: GroupMessageEvent):
    try:
        message_text = event.get_plaintext().strip()
        if message_text != "转文字":
            return

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

        messages = [("转文字结果", "text", original_text)]
        forward_nodes = await create_forward_message(bot, group_id, messages)

        await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)
        text_image_cache.remove_cache_by_image_hash(image_hash, group_id)

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理转文字命令时出错: {e}")
        await convert_to_text.finish("转换失败，请稍后重试喵！")
