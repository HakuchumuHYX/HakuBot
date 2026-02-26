import random
import os
import hashlib
from nonebot import on_notice, logger
from nonebot.adapters.onebot.v11 import (
    PokeNotifyEvent, Message, MessageSegment, Bot
)
from nonebot.exception import FinishedException

from ..config import is_text_to_image_enabled, TEXT_TO_IMAGE_LENGTH_THRESHOLD
from ..models.data import data_manager
from ..models.cache import message_cache, text_image_cache
from ..utils.common import get_group_id
from ..services.text import convert_text_to_image
from plugins.plugin_manager.enable import is_feature_enabled
from plugins.plugin_manager.cd_manager import check_cd, update_cd
from plugins.utils.image_utils import path_to_base64_image

poke = on_notice()

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
    """处理戳一戳事件"""
    if event.notice_type != "notify" or event.sub_type != "poke" or event.target_id != event.self_id:
        return

    group_id = get_group_id(event)
    if group_id == 0:
        return

    user_id = str(event.user_id)
    # 检查插件功能开关
    if not is_feature_enabled("poke_reply", "poke", str(group_id), user_id):
        return

    # 接入外部CD管理
    PLUGIN_ID = "poke_reply:poke"
    is_superuser = str(user_id) in bot.config.superusers

    if not is_superuser:
        remaining_time = check_cd(PLUGIN_ID, str(group_id), str(user_id))
        if remaining_time > 0:
            logger.info(f"群 {group_id} 用户 {user_id} 戳一戳CD中，剩余 {remaining_time}秒")
            return
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
                    message=Message(path_to_base64_image(image_path))
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
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理戳一戳事件时发生错误: {e}")
