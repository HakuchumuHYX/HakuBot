from nonebot import on_command, on_message, logger
from nonebot.adapters.onebot.v11 import Message, GroupMessageEvent, MessageEvent, Bot
from nonebot.rule import Rule, to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException

from plugins.poke_reply.models.cache import message_cache, text_image_cache
from plugins.poke_reply.utils.common import (
    get_group_id,
    extract_image_data,
    ensure_at_me,
    create_forward_message,
)
from utils.network.images import fetch_image
from plugins.poke_reply.services.contribute import (
    handle_text_contribution,
    handle_image_contribution,
)
from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from utils.onebot.forward import send_forward_msg
from utils.onebot.rules import command_boundary


# --- 注册匹配器 ---
def is_convert_to_text_message(event: MessageEvent) -> bool:
    return event.get_plaintext().strip() == "转文字"


async def _convert_to_text_rule(event: MessageEvent) -> bool:
    return is_convert_to_text_message(event)


PLUGIN_ID_CONTRIB = "poke_reply:contribute"

contribute = on_command(
    "投稿",
    rule=ensure_at_me() & to_me() & command_boundary & switch_rule(PLUGIN_ID_CONTRIB),
    priority=5,
    block=True,
)
convert_to_text = on_message(
    rule=to_me() & Rule(_convert_to_text_rule) & switch_rule("poke_reply"),
    priority=10,
    block=True,
)


# --- 投稿处理器 ---


@contribute.handle()
async def handle_contribute(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
):
    group_id = get_group_id(event)
    if group_id == 0:
        await contribute.finish("请在群聊中使用投稿功能喵！")

    remaining_time = cooldown_remaining(PLUGIN_ID_CONTRIB, event)
    if remaining_time > 0:
        await contribute.finish(f"投稿功能冷却中，请等待 {remaining_time} 秒喵！")

    replied_message = event.reply
    target_message = event.get_message()

    if replied_message:
        target_message = replied_message.message
        has_images, images = extract_image_data(target_message)
        if has_images:
            await process_image_contribution(bot, event, group_id, images)
        else:
            text_to_add = target_message.extract_plain_text().strip()
            if not text_to_add:
                await contribute.finish("回复的消息没有文本内容喵！")
            await process_text_contribution(bot, event, group_id, text_to_add)
    else:
        has_images, images = extract_image_data(target_message)
        if has_images:
            await process_image_contribution(bot, event, group_id, images)
        else:
            args_text = args.extract_plain_text().strip()
            await process_text_contribution(bot, event, group_id, args_text)


async def process_text_contribution(
    bot: Bot, event: GroupMessageEvent, group_id: int, text: str
):
    success, message = await handle_text_contribution(group_id, text)
    if success:
        mark_cooldown(PLUGIN_ID_CONTRIB, event)
        result = await bot.send_group_msg(group_id=group_id, message=message)
        message_cache.add_message(
            group_id=group_id,
            message_id=result["message_id"],
            content=text,
            message_type="contribute_text",
        )
    else:
        await contribute.finish(message)


async def process_image_contribution(
    bot: Bot, event: GroupMessageEvent, group_id: int, images: list
):
    success, message, saved_filenames = await handle_image_contribution(
        group_id, images
    )
    if success:
        mark_cooldown(PLUGIN_ID_CONTRIB, event)
        result = await bot.send_group_msg(group_id=group_id, message=message)
        content_info = f"图片投稿: {', '.join(saved_filenames)}"
        message_cache.add_message(
            group_id=group_id,
            message_id=result["message_id"],
            content=content_info,
            message_type="contribute_image",
        )
    else:
        await contribute.finish(message)


# --- 转文字 ---


@convert_to_text.handle()
async def handle_convert_to_text(bot: Bot, event: GroupMessageEvent):
    try:
        message_text = event.get_plaintext().strip()
        if message_text != "转文字":
            return

        if not hasattr(event, "reply") or event.reply is None:
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

        try:
            image_hash = (await fetch_image(image_url)).md5
        except Exception:
            await convert_to_text.finish("下载图片失败，无法计算哈希值喵！")

        cache_record = text_image_cache.get_cache_by_image_hash(image_hash, group_id)
        if not cache_record:
            await convert_to_text.finish(
                "未找到对应的文本缓存，可能已过期或不是由长文本转换的图片喵！"
            )

        original_text = cache_record.get("original_text", "")
        if not original_text:
            await convert_to_text.finish("缓存文本为空，无法转换喵！")

        messages = [("转文字结果", "text", original_text)]
        forward_nodes = await create_forward_message(bot, group_id, messages)

        await send_forward_msg(bot, group_id=group_id, items=forward_nodes)
        text_image_cache.remove_cache_by_image_hash(image_hash, group_id)

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"处理转文字命令时出错: {e}")
        await convert_to_text.finish("转换失败，请稍后重试喵！")
