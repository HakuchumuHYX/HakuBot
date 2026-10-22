"""SU 命令：删除审批、查看所有投稿数、清除投稿重复、文本转图片"""

import asyncio
import time

from nonebot import get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type, to_me

from plugins.poke_reply import dedupe, store
from plugins.poke_reply.config import (
    DEFAULT_TEXT_TO_IMAGE_THRESHOLD,
    MIN_TEXT_TO_IMAGE_THRESHOLD,
)
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment

DECISIONS = {
    "同意": "approved",
    "approve": "approved",
    "拒绝": "rejected",
    "reject": "rejected",
}
KIND_TEXT = {"text": "文本", "image": "图片"}


def _preview(content: dict) -> str:
    if content["kind"] == "image":
        return "[图片] " + ", ".join(content["filenames"])
    text = content["text"]
    return text[:100] + "..." if len(text) > 100 else text


def _format_time(timestamp: float, fmt: str) -> str:
    return time.strftime(fmt, time.localtime(timestamp))


# --- 删除申请 ---


async def notify_superusers(bot: Bot, request: dict) -> None:
    """私聊通知每个 SU；通知消息 ID 记进申请，SU 直接回复通知即可审批"""
    content = request["content"]
    notice = (
        f"📝 新的删除申请\n"
        f"申请ID: {request['request_id']}\n"
        f"群组: {request['group_id']}\n"
        f"申请人: {request['requester_id']}\n"
        f"内容类型: {KIND_TEXT[content['kind']]}\n"
        f"申请时间: {_format_time(request['request_time'], '%Y-%m-%d %H:%M:%S')}\n\n"
        f"请回复 '同意' 或 '拒绝'，或使用命令:\n"
        f"处理删除 {request['request_id']} 同意/拒绝\n\n"
    )
    preview = None
    if content["kind"] == "text":
        notice += f"内容预览: {_preview(content)}"
    else:
        path = store.image_dir(request["group_id"]) / content["filenames"][0]
        if path.exists():
            notice += "图片预览:"
            preview = Message(image_segment(path))
        else:
            notice += f"图片文件不存在: {path.name}"

    notice_ids = []
    for superuser in get_driver().config.superusers:
        try:
            receipt = await bot.send_private_msg(user_id=int(superuser), message=notice)
            notice_ids.append(receipt["message_id"])
            if preview:
                receipt = await bot.send_private_msg(
                    user_id=int(superuser), message=preview
                )
                notice_ids.append(receipt["message_id"])
        except Exception as e:
            logger.warning(
                f"向超级用户 {superuser} 发送删除申请通知失败: {get_exc_desc(e)}"
            )
    store.set_notice_ids(request["request_id"], notice_ids)


async def _decide(bot: Bot, request: dict, decision: str, processor_id: int) -> str:
    if request["status"] != "pending":
        return "该申请已被处理过了喵！"
    status = DECISIONS[decision]
    store.finish_request(request["request_id"], status, processor_id)

    result = "同意" if status == "approved" else "拒绝"
    group_message = (
        f"删除申请 {request['request_id']} 已{result}处理\n"
        f"申请人: {request['requester_id']}\n"
    )
    if status == "approved":
        deleted = store.remove_content(request["group_id"], request["content"])
        store.forget(request["group_id"], request["message_id"])
        group_message += (
            "✅ 内容已成功删除" if deleted else "❌ 删除失败，内容可能不存在"
        )
    await bot.send_group_msg(group_id=request["group_id"], message=group_message)
    return f"已{result}删除申请 {request['request_id']}"


su_direct_delete = on_command(
    "poke删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)
handle_delete_request = on_command(
    "处理删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)
view_delete_requests = on_command(
    "查看删除申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)
clear_processed_requests = on_command(
    "清理已处理申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)
# SU 私聊回复申请通知“同意/拒绝”
notice_reply = on_message(
    rule=to_me() & is_type(PrivateMessageEvent),
    permission=SUPERUSER,
    priority=10,
    block=False,
)


@su_direct_delete.handle()
async def handle_su_direct_delete(matcher: Matcher, event: GroupMessageEvent):
    if event.reply is None:
        await matcher.finish("请回复要删除的消息并说'poke删除'喵！")
    record = store.recent(event.group_id, event.reply.message_id)
    if record is None:
        await matcher.finish("该消息已过期或未被缓存，无法自动删除喵！")
    if not store.remove_content(event.group_id, record["content"]):
        await matcher.finish("❌ 删除失败，内容可能不存在或文件已被移除。")
    store.forget(event.group_id, event.reply.message_id)
    await matcher.finish("✅ 内容已成功删除！")


@handle_delete_request.handle()
async def handle_process_delete(
    bot: Bot, matcher: Matcher, event: PrivateMessageEvent, arg: Message = CommandArg()
):
    parts = arg.extract_plain_text().split()
    if len(parts) < 2:
        await matcher.finish("使用方法: 处理删除 <申请ID> <同意/拒绝>")
    request_id, decision = parts[0], parts[1].lower()
    if decision not in DECISIONS:
        await matcher.finish("请使用'同意'或'拒绝'喵！")
    request = store.get_request(request_id)
    if request is None:
        await matcher.finish("未找到该删除申请喵！")
    await matcher.finish(await _decide(bot, request, decision, event.user_id))


@notice_reply.handle()
async def handle_notice_reply(bot: Bot, matcher: Matcher, event: PrivateMessageEvent):
    decision = event.get_plaintext().strip().lower()
    if event.reply is None or decision not in DECISIONS:
        return
    request = store.request_by_notice(event.reply.message_id)
    if request is None:
        return
    await matcher.finish(await _decide(bot, request, decision, event.user_id))


@view_delete_requests.handle()
async def handle_view_requests(matcher: Matcher):
    pending = store.pending_requests()
    if not pending:
        await matcher.finish("当前没有待处理的删除申请喵！")
    message = "📋 待处理的删除申请:\n\n"
    for index, request in enumerate(pending, 1):
        message += (
            f"{index}. 申请ID: {request['request_id']}\n"
            f"   群组: {request['group_id']}\n"
            f"   申请人: {request['requester_id']}\n"
            f"   内容: {_preview(request['content'])}\n"
            f"   申请时间: {_format_time(request['request_time'], '%m-%d %H:%M')}\n\n"
        )
    await matcher.finish(message)


@clear_processed_requests.handle()
async def handle_clear_processed(matcher: Matcher):
    cleared = store.clear_processed_requests()
    await matcher.finish(
        f"已清理 {cleared} 个已处理的申请，剩余 {store.request_count()} 个申请喵！"
    )


# --- 统计与清理 ---

view_all_counts = on_command(
    "查看所有投稿数", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)
clear_duplicates = on_command(
    "清除投稿重复", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)


@view_all_counts.handle()
async def handle_view_all_counts(matcher: Matcher):
    counts = [
        (group_id, len(store.texts(group_id)), len(store.images(group_id)))
        for group_id in store.group_ids()
    ]
    counts = [c for c in counts if c[1] or c[2]]
    if not counts:
        await matcher.finish("还没有任何群聊有投稿内容喵！")
    total_texts = sum(c[1] for c in counts)
    total_images = sum(c[2] for c in counts)
    details = [f"群 {gid}: {texts}文/{images}图" for gid, texts, images in counts]
    message = (
        f"共 {len(counts)} 个群聊有投稿，总计 {total_texts + total_images} 个内容喵！\n"
        f"📝 文本: {total_texts} 条\n"
        f"🖼️  图片: {total_images} 张\n\n"
    )
    if len(details) <= 10:
        message += "各群详情:\n" + "\n".join(details)
    else:
        message += "前10个群组详情:\n" + "\n".join(details[:10])
        message += f"\n... 还有 {len(details) - 10} 个群聊"
    await matcher.finish(message)


@clear_duplicates.handle()
async def handle_clear_duplicates(matcher: Matcher, event: GroupMessageEvent):
    await matcher.send("正在开始检查本群所有图片，这可能需要几分钟，请稍候...")
    pairs = await asyncio.to_thread(
        dedupe.find_duplicate_pairs, store.images(event.group_id)
    )
    if not pairs:
        await matcher.finish("检查完毕，未在本群发现重复的图片喵！")
    duplicate = {"kind": "image", "filenames": [path.name for _, path in pairs]}
    store.remove_content(event.group_id, duplicate)
    await matcher.finish(
        f"清理完成！共删除了 {len(pairs)} 张重复图片，每组保留最早的那张。"
    )


# --- 文本转图片 ---

text_to_image = on_command(
    "文本转图片", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)


@text_to_image.handle()
async def handle_text_to_image(
    matcher: Matcher, event: GroupMessageEvent, arg: Message = CommandArg()
):
    group_id = event.group_id
    option = arg.extract_plain_text().strip()
    threshold = store.text_to_image_threshold(group_id)
    if not option:
        status = f"已开启，超过 {threshold} 字转成图片" if threshold else "未开启"
        await matcher.finish(f"本群文本转图片：{status}")
    if option == "关":
        store.set_text_to_image(group_id, None)
        await matcher.finish("已关闭本群文本转图片喵！")

    if option == "开":
        threshold = threshold or DEFAULT_TEXT_TO_IMAGE_THRESHOLD
    elif option.isdecimal():
        threshold = int(option)
        if threshold < MIN_TEXT_TO_IMAGE_THRESHOLD:
            await matcher.finish(f"阈值不能小于{MIN_TEXT_TO_IMAGE_THRESHOLD}字喵！")
    else:
        await matcher.finish("用法：文本转图片 [开/关/字数]")
    store.set_text_to_image(group_id, threshold)
    await matcher.finish(f"已开启本群文本转图片，超过 {threshold} 字转成图片喵！")
