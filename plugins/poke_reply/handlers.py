"""群友命令：戳一戳、投稿、转文字、申请删除、查看投稿"""

import asyncio
import random
from collections.abc import Callable
from pathlib import Path

from nonebot import logger, on_command, on_message, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PokeNotifyEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import Rule, to_me

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from plugins.poke_reply import dedupe, store
from plugins.poke_reply.admin import notify_superusers
from plugins.poke_reply.config import MAX_TEXT_LENGTH
from utils.logging import get_exc_desc
from utils.network.images import fetch_image
from utils.onebot.forward import ForwardItem, send_forward_msg
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from utils.rendering.engine import render_markdown

POKE_ID = "poke_reply:poke"
CONTRIBUTE_ID = "poke_reply:contribute"

MAX_NODES_PER_FORWARD = 30
MAX_TEXT_LENGTH_PER_FORWARD = 20000
MAX_IMAGES_PER_FORWARD = 15

# 模拟聊天气泡；markdown 允许内联 HTML，样式直接拼在正文后面
BUBBLE_CSS = """
<style>
    body {
        font-family: "Noto Sans CJK SC", sans-serif;
        background-color: #f2f2f2;
        padding: 20px;
    }
    .markdown-body {
        background-color: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        font-size: 16px;
        line-height: 1.6;
        color: #333;
    }
</style>
"""


async def _really_at_me(event: GroupMessageEvent) -> bool:
    # to_me 把“回复 bot 的消息”也算进去，投稿、查看全部要求真的 @ 了 bot
    return any(
        segment.type == "at" and segment.data.get("qq") == str(event.self_id)
        for segment in event.original_message
    )


at_me = Rule(_really_at_me) & to_me()


def _summary(group_id: int) -> str:
    text_count, image_count = len(store.texts(group_id)), len(store.images(group_id))
    return f"当前群共有{text_count}条文本和{image_count}张图片喵"


# --- 戳一戳 ---

poke = on_notice(rule=switch_rule(POKE_ID), priority=1, block=False)


async def _text_message(group_id: int, text: str) -> tuple[MessageSegment, bool]:
    """长文本在开启的群里转成图片；返回 (消息段, 是否转了图片)"""
    threshold = store.text_to_image_threshold(group_id)
    if threshold is None or len(text) <= threshold:
        return MessageSegment.text(text), False
    try:
        # 行尾两个空格让 markdown 保留原文换行
        image = await render_markdown(
            text.replace("\n", "  \n") + BUBBLE_CSS, width=500
        )
    except Exception as e:
        logger.opt(exception=True).warning(f"长文本转图片失败，改发文本: {e}")
        return MessageSegment.text(text), False
    return image_segment(image), True


@poke.handle()
async def handle_poke(bot: Bot, event: PokeNotifyEvent):
    if event.target_id != event.self_id or event.group_id is None:
        return
    if cooldown_remaining(POKE_ID, event) > 0:
        return

    group_id = event.group_id
    texts, images = store.texts(group_id), store.images(group_id)
    if not texts and not images:
        await bot.send_group_msg(
            group_id=group_id, message="这个群还没有投稿内容喵，快来投稿吧！"
        )
        return

    # 按文本数、图片数加权
    if random.randrange(len(texts) + len(images)) < len(texts):
        text = random.choice(texts)
        segment, rendered = await _text_message(group_id, text)
        content = {"kind": "text", "text": text}
    else:
        path = random.choice(images)
        segment, rendered = image_segment(path), False
        content = {"kind": "image", "filenames": [path.name]}

    result = await bot.send_group_msg(group_id=group_id, message=Message(segment))
    store.remember(group_id, result["message_id"], content, rendered)
    # 成功发出投稿内容才扣 CD，提示语不扣
    mark_cooldown(POKE_ID, event)


# --- 投稿 ---

contribute = on_command(
    "投稿",
    rule=at_me & command_boundary & switch_rule(CONTRIBUTE_ID),
    priority=5,
    block=True,
)


async def _save_text(matcher: Matcher, group_id: int, text: str) -> dict:
    if len(text) > MAX_TEXT_LENGTH:
        await matcher.finish(f"文本太长了，请控制在{MAX_TEXT_LENGTH}字以内喵！")
    if dedupe.similar_text_exists(text, store.texts(group_id)):
        await matcher.finish("投稿失败，本群已经有类似的话了喵！")
    store.add_text(group_id, text)
    return {"kind": "text", "text": text}


async def _save_images(matcher: Matcher, group_id: int, urls: list[str]) -> dict:
    downloaded = []
    for url in urls:
        try:
            image = await fetch_image(url)
        except Exception as e:
            logger.warning(f"下载投稿图片失败: {url}: {get_exc_desc(e)}")
            await matcher.finish("图片投稿失败，下载图片时出错喵")
        duplicate = await asyncio.to_thread(
            dedupe.find_duplicate, image.data, store.images(group_id)
        )
        if duplicate:
            logger.info(f"群 {group_id} 图片投稿重复: {duplicate.name}")
            await matcher.finish("投稿失败，本群已经有类似的图片了喵！")
        downloaded.append(image)
    filenames = [store.add_image(group_id, i.data, i.extension) for i in downloaded]
    return {"kind": "image", "filenames": filenames}


@contribute.handle()
async def handle_contribute(
    bot: Bot, matcher: Matcher, event: GroupMessageEvent, arg: Message = CommandArg()
):
    remaining = cooldown_remaining(CONTRIBUTE_ID, event)
    if remaining > 0:
        await matcher.finish(f"投稿功能冷却中，请等待 {remaining} 秒喵！")

    group_id = event.group_id
    # 回复一条消息投稿时投被回复的那条，否则投本条；QQ 表情不算图片，按文本投时丢掉
    source = event.reply.message if event.reply else event.get_message()
    urls = [seg.data.get("url", "") for seg in source if seg.type == "image"]
    if urls:
        content = await _save_images(matcher, group_id, urls)
        message = f"图片投稿成功！成功上传{len(urls)}张图片！{_summary(group_id)}"
    else:
        text = (source if event.reply else arg).extract_plain_text().strip()
        if not text:
            await matcher.finish(
                "回复的消息没有文本内容喵！"
                if event.reply
                else "请提供要投稿的内容，格式：@我 投稿 你的文本，或回复一条消息 @我 投稿"
            )
        content = await _save_text(matcher, group_id, text)
        message = f"文本投稿成功！{_summary(group_id)}"

    mark_cooldown(CONTRIBUTE_ID, event)
    # 回执也记下来，投错了可以回复回执申请删除
    result = await bot.send_group_msg(group_id=group_id, message=message)
    store.remember(group_id, result["message_id"], content)


# --- 转文字 ---


async def _is_convert_to_text(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip() == "转文字"


convert_to_text = on_message(
    rule=to_me() & Rule(_is_convert_to_text) & switch_rule("poke_reply"),
    priority=10,
    block=True,
)


@convert_to_text.handle()
async def handle_convert_to_text(bot: Bot, matcher: Matcher, event: GroupMessageEvent):
    if event.reply is None:
        await matcher.finish("请回复要转换的图片消息并说'转文字'喵！")
    record = store.recent(event.group_id, event.reply.message_id)
    if record is None or not record["rendered"]:
        await matcher.finish("未找到对应的文本，可能已过期或不是由长文本转换的图片喵！")
    text = MessageSegment.text(record["content"]["text"])
    await send_forward_msg(
        bot, group_id=event.group_id, items=[ForwardItem(text, name="转文字结果")]
    )


# --- 申请删除 ---

apply_delete = on_command(
    "申请删除",
    rule=to_me() & command_boundary & switch_rule("poke_reply"),
    priority=5,
    block=True,
)


@apply_delete.handle()
async def handle_apply_delete(bot: Bot, matcher: Matcher, event: GroupMessageEvent):
    if event.reply is None:
        await matcher.finish("请回复要删除的消息并说'申请删除'喵！")
    message_id = event.reply.message_id
    record = store.recent(event.group_id, message_id)
    if record is None:
        await matcher.finish("该消息已超过10分钟有效期，无法申请删除喵！")
    request = store.add_request(
        event.group_id, message_id, event.user_id, record["content"]
    )
    await notify_superusers(bot, request)
    await matcher.finish(
        f"删除申请已提交 (ID: {request['request_id']})，等待管理员处理喵！"
    )


# --- 查看 ---

view_stats = on_command(
    "查看投稿统计",
    rule=to_me() & command_boundary & switch_rule("poke_reply"),
    priority=5,
    block=True,
)
view_all = on_command(
    "查看所有投稿",
    rule=at_me & command_boundary & switch_rule("poke_reply"),
    priority=5,
    block=True,
)
view_all_texts = on_command(
    "查看所有文本",
    rule=at_me & command_boundary & switch_rule("poke_reply"),
    priority=5,
    block=True,
)
view_all_images = on_command(
    "查看所有图片",
    rule=at_me & command_boundary & switch_rule("poke_reply"),
    priority=5,
    block=True,
)


@view_stats.handle()
async def handle_view_stats(matcher: Matcher, event: GroupMessageEvent):
    text_count = len(store.texts(event.group_id))
    image_count = len(store.images(event.group_id))
    total = text_count + image_count
    if total == 0:
        await matcher.finish("当前群还没有任何投稿内容喵！")
    text_ratio = text_count / total * 100
    image_ratio = image_count / total * 100
    await matcher.finish(
        f"📊 投稿统计详情：\n"
        f"📝 文本数量：{text_count} 条 ({text_ratio:.1f}%)\n"
        f"🖼️  图片数量：{image_count} 张 ({image_ratio:.1f}%)\n"
        f"📦 总计：{total} 个内容\n\n"
        f"戳一戳时：\n"
        f"• 文本发送概率：{text_ratio:.1f}%\n"
        f"• 图片发送概率：{image_ratio:.1f}%"
    )


def _text_batches(texts: list[str]) -> list[list[str]]:
    """编号后分批：每批不超过 30 条、总长不超过 20000 字；单条超长的自成一批"""
    batches, batch, length = [], [], 0
    for index, text in enumerate(texts, 1):
        item = f"{index}. {text}"
        if batch and (
            len(batch) >= MAX_NODES_PER_FORWARD
            or length + len(item) > MAX_TEXT_LENGTH_PER_FORWARD
        ):
            batches.append(batch)
            batch, length = [], 0
        batch.append(item)
        length += len(item)
    if batch:
        batches.append(batch)
    return batches


async def _send_batches(
    bot: Bot,
    group_id: int,
    title: str,
    batches: list[list],
    to_segment: Callable[..., MessageSegment],
) -> None:
    for index, batch in enumerate(batches, 1):
        heading = title
        if len(batches) > 1:
            heading += f" - 第{index}批/共{len(batches)}批"
        segments = [MessageSegment.text(heading)] + [to_segment(x) for x in batch]
        items = [ForwardItem(segment, name="投稿内容") for segment in segments]
        await send_forward_msg(bot, group_id=group_id, items=items)
        if index < len(batches):
            await asyncio.sleep(1)


async def _send_texts(bot: Bot, group_id: int, texts: list[str], title: str) -> None:
    await _send_batches(
        bot, group_id, f"📋 {title}", _text_batches(texts), MessageSegment.text
    )


async def _send_images(bot: Bot, group_id: int, images: list[Path], title: str) -> None:
    batches = [
        images[i : i + MAX_IMAGES_PER_FORWARD]
        for i in range(0, len(images), MAX_IMAGES_PER_FORWARD)
    ]
    await _send_batches(bot, group_id, f"🖼️ {title}", batches, image_segment)


@view_all.handle()
async def handle_view_all(bot: Bot, matcher: Matcher, event: GroupMessageEvent):
    texts, images = store.texts(event.group_id), store.images(event.group_id)
    if not texts and not images:
        await matcher.finish("本群还没有任何投稿内容喵！")
    if texts:
        await _send_texts(bot, event.group_id, texts, "所有投稿文本")
    if texts and images:
        await asyncio.sleep(2)
    if images:
        await _send_images(bot, event.group_id, images, "所有投稿图片")


@view_all_texts.handle()
async def handle_view_all_texts(bot: Bot, matcher: Matcher, event: GroupMessageEvent):
    texts = store.texts(event.group_id)
    if not texts:
        await matcher.finish("本群还没有文本投稿喵！")
    await _send_texts(bot, event.group_id, texts, "所有文本投稿")


@view_all_images.handle()
async def handle_view_all_images(bot: Bot, matcher: Matcher, event: GroupMessageEvent):
    images = store.images(event.group_id)
    if not images:
        await matcher.finish("本群还没有图片投稿喵！")
    await _send_images(bot, event.group_id, images, "所有图片投稿")
