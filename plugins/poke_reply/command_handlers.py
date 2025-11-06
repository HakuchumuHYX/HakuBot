# poke_reply/command_handlers.py
import time
import asyncio
import re
import hashlib
from typing import Tuple, List
from pathlib import Path

from nonebot import on_command, logger, get_bot, get_driver
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    PrivateMessageEvent,
    Message,
    MessageSegment,
    Bot,
    MessageEvent
)
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

# vvvvvv 【修改：导入路径】 vvvvvv
from .data_manager import data_manager
from .managers import message_cache, delete_request_manager, text_image_cache
from .config import (
    TEXT_FILES_DIR, IMAGE_FILES_DIR, get_group_image_dir,
    add_text_to_image_group, remove_text_to_image_group,
    is_text_to_image_enabled, set_text_to_image_threshold,
    get_text_to_image_threshold
)
from .common import (
    download_and_hash_image, ensure_at_me,
    create_forward_message, image_to_base64
)
from .text_to_image import HTMLRENDER_AVAILABLE
# ^^^^^^ 【修改：导入路径】 ^^^^^^

# ... (文件其余部分保持不变) ...
# --- 统计命令 (来自 stat_handlers.py) ---
view_text_count = on_command("查看投稿数", rule=to_me(), priority=5, block=True)
view_all_text_count = on_command("查看所有投稿数", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_content_stats = on_command("查看投稿统计", rule=to_me(), priority=5, block=True)


@view_text_count.handle()
async def handle_view_text_count(event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await view_text_count.finish("请在群聊中使用此命令喵！")
    group_id = event.group_id
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_text_count.finish("数据加载失败，无法查看文本数喵！")
    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    await view_text_count.finish(f"当前群共有 {text_count} 条文本和 {image_count} 张图片喵！")


@view_content_stats.handle()
async def handle_view_content_stats(event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await view_content_stats.finish("请在群聊中使用此命令喵！")
    group_id = event.group_id
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_content_stats.finish("数据加载失败，无法查看统计喵！")
    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    total_count = text_count + image_count
    if total_count == 0:
        await view_content_stats.finish("当前群还没有任何投稿内容喵！")
    text_ratio = (text_count / total_count) * 100
    image_ratio = (image_count / total_count) * 100
    message = (
        f"📊 投稿统计详情：\n"
        f"📝 文本数量：{text_count} 条 ({text_ratio:.1f}%)\n"
        f"🖼️  图片数量：{image_count} 张 ({image_ratio:.1f}%)\n"
        f"📦 总计：{total_count} 个内容\n\n"
        f"戳一戳时：\n"
        f"• 文本发送概率：{text_ratio:.1f}%\n"
        f"• 图片发送概率：{image_ratio:.1f}%"
    )
    await view_content_stats.finish(message)


@view_all_text_count.handle()
async def handle_view_all_text_count(event: MessageEvent):
    try:
        total_groups = 0
        total_texts = 0
        total_images = 0
        group_details = []
        for file_path in TEXT_FILES_DIR.glob("text_*.json"):
            try:
                filename = file_path.stem
                group_id = int(filename[5:])
                if data_manager.ensure_group_data_loaded(group_id):
                    text_count = data_manager.get_text_count(group_id)
                    image_count = data_manager.get_image_count(group_id)
                    total_texts += text_count
                    total_images += image_count
                    total_groups += 1
                    if text_count > 0 or image_count > 0:
                        group_details.append(f"群 {group_id}: {text_count}文/{image_count}图")
            except (ValueError, Exception) as e:
                logger.warning(f"处理文件 {file_path} 时出错: {e}")
        if total_groups == 0:
            message = "还没有任何群聊有投稿内容喵！"
        else:
            total_content = total_texts + total_images
            message = f"共 {total_groups} 个群聊有投稿，总计 {total_content} 个内容喵！\n"
            message += f"📝 文本: {total_texts} 条\n"
            message += f"🖼️  图片: {total_images} 张\n\n"
            if len(group_details) <= 10:
                message += "各群详情:\n" + "\n".join(group_details)
            else:
                message += "前10个群组详情:\n" + "\n".join(group_details[:10])
                message += f"\n... 还有 {len(group_details) - 10} 个群聊"
        await view_all_text_count.finish(message)
    except Exception as e:
        logger.error(f"获取所有群聊投稿统计时出错: {e}")
        await view_all_text_count.finish("获取统计信息时出错喵！")


# --- 查看投稿命令 (来自 view_contributions.py) ---
view_all_contributions = on_command("查看所有投稿", rule=ensure_at_me() & to_me(), priority=5, block=True)
view_all_texts = on_command("查看所有文本", rule=ensure_at_me() & to_me(), priority=5, block=True)
view_all_images = on_command("查看所有图片", rule=ensure_at_me() & to_me(), priority=5, block=True)

MAX_NODES_PER_FORWARD = 30
MAX_TEXT_LENGTH_PER_NODE = 20000
MAX_IMAGES_PER_BATCH = 15


async def send_text_forward_message(bot: Bot, group_id: int, texts: List[str], title: str = "文本投稿") -> bool:
    try:
        if not texts:
            await bot.send_group_msg(group_id=group_id, message=f"本群还没有{title}喵！")
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
            if (len(current_batch) >= MAX_NODES_PER_FORWARD or
                    current_batch_length + len(text_with_number) > MAX_TEXT_LENGTH_PER_NODE):
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
            batch_title = f"📋 {title}" + (f" - 第{batch_index}批/共{total_batches}批" if total_batches > 1 else "")
            messages.append(("投稿内容", "text", batch_title))
            for text_item in batch:
                messages.append(("投稿内容", "text", text_item))

            # 使用 common.py 中的 create_forward_message
            forward_nodes = await create_forward_message(bot, group_id, messages)
            await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)
            if batch_index < total_batches:
                await asyncio.sleep(1)
        return True
    except Exception as e:
        logger.error(f"发送文本合并转发消息失败: {e}")
        return False


async def send_image_forward_message(bot: Bot, group_id: int, image_filenames: List[str],
                                     title: str = "图片投稿") -> bool:
    try:
        if not image_filenames:
            await bot.send_group_msg(group_id=group_id, message=f"本群还没有{title}喵！")
            return True
        batches = [image_filenames[i:i + MAX_IMAGES_PER_BATCH] for i in
                   range(0, len(image_filenames), MAX_IMAGES_PER_BATCH)]

        total_batches = len(batches)
        for batch_index, batch in enumerate(batches, 1):
            messages = []
            batch_title = f"🖼️ {title}" + (f" - 第{batch_index}批/共{total_batches}批" if total_batches > 1 else "")
            messages.append(("投稿内容", "text", batch_title))

            for filename in batch:
                image_path = get_group_image_dir(group_id) / filename
                if image_path.exists():
                    success, base64_data = image_to_base64(image_path)
                    if success:
                        messages.append(("投稿内容", "image", base64_data))
                    else:
                        logger.warning(f"图片转换失败: {filename}, 错误: {base64_data}")
                else:
                    logger.warning(f"图片文件不存在: {image_path}")

            # 使用 common.py 中的 create_forward_message
            forward_nodes = await create_forward_message(bot, group_id, messages)
            await bot.send_group_forward_msg(group_id=group_id, messages=forward_nodes)
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
            if not await send_text_forward_message(bot, group_id, texts, "所有投稿文本"): return
            await asyncio.sleep(2)
        if images:
            if not await send_image_forward_message(bot, group_id, images, "所有投稿图片"): return
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


# --- 删除管理命令 (来自 command_handlers.py 原文件) ---
apply_delete = on_command("申请删除", rule=to_me(), priority=5, block=True)
handle_delete_request = on_command("处理删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_delete_requests = on_command("查看删除申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
clear_processed_requests = on_command("清理已处理申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


async def get_image_preview(group_id: int, content: str, message_type: str) -> Tuple[bool, str]:
    try:
        if message_type == "image":
            image_dir = get_group_image_dir(group_id)
            image_path = image_dir / content
            return (True, str(image_path)) if image_path.exists() else (False, f"图片文件不存在: {content}")
        elif message_type == "contribute_image":
            if "图片投稿:" in content:
                parts = content.split(": ")
                if len(parts) > 1:
                    filename = parts[1].split(", ")[0]
                    image_dir = get_group_image_dir(group_id)
                    image_path = image_dir / filename
                    return (True, str(image_path)) if image_path.exists() else (
                    False, f"投稿图片文件不存在: {filename}")
        return False, "不支持的消息类型或格式错误"
    except Exception as e:
        logger.error(f"获取图片预览失败: {e}")
        return False, f"获取图片预览失败: {str(e)}"


async def notify_superuser(bot: Bot, request_info: dict):
    try:
        superusers = list(get_driver().config.superusers)
        base_message = (
            f"📝 新的删除申请\n"
            f"申请ID: {request_info['request_id']}\n"
            f"群组: {request_info['group_id']}\n"
            f"消息ID: {request_info['message_id']}\n"
            f"申请人: {request_info['requester_id']}\n"
            f"内容类型: {request_info['type']}\n"
            f"申请时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(request_info['request_time']))}\n\n"
            f"请使用命令处理:\n"
            f"处理删除 {request_info['request_id']} 同意/拒绝"
        )

        image_preview_sent = False
        if request_info['type'] in ["image", "contribute_image"]:
            success, image_path_or_error = await get_image_preview(request_info['group_id'], request_info['content'],
                                                                   request_info['type'])
            if success:
                for superuser in superusers:
                    try:
                        await bot.send_private_msg(user_id=int(superuser), message=base_message + f"\n\n图片预览:")
                        await bot.send_private_msg(user_id=int(superuser), message=Message(
                            MessageSegment.image(f"file:///{image_path_or_error}")))
                        image_preview_sent = True
                    except Exception as e:
                        logger.error(f"向超级用户 {superuser} 发送图片预览失败: {e}")
                        await bot.send_private_msg(user_id=int(superuser),
                                                   message=base_message + f"\n\n图片预览发送失败: {str(e)}")
            else:
                for superuser in superusers:
                    await bot.send_private_msg(user_id=int(superuser),
                                               message=base_message + f"\n\n{image_path_or_error}")

        if not image_preview_sent and request_info['type'] not in ["image", "contribute_image"]:
            content_preview = request_info['content'][:100] + "..." if len(request_info['content']) > 100 else \
            request_info['content']
            final_message = base_message + f"\n\n内容预览: {content_preview}"
            for superuser in superusers:
                await bot.send_private_msg(user_id=int(superuser), message=final_message)
    except Exception as e:
        logger.error(f"通知超级用户失败: {e}")


def find_similar_text(group_id: int, target_content: str, threshold: float = 0.9) -> Tuple[bool, str]:
    try:
        if group_id not in data_manager.group_texts:
            return False, ""

        def preprocess_text(text):
            text = re.sub(r'[^\w]', '', text)
            return text.lower()

        target_processed = preprocess_text(target_content)
        for text in data_manager.group_texts[group_id]:
            text_processed = preprocess_text(text)
            if len(target_processed) == 0 or len(text_processed) == 0: continue
            set_target = set(target_processed)
            set_text = set(text_processed)
            intersection = len(set_target & set_text)
            union = len(set_target | set_text)
            similarity = intersection / union if union > 0 else 0
            if similarity >= threshold:
                return True, text
        return False, ""
    except Exception as e:
        logger.error(f"查找相似文本时出错: {e}")
        return False, ""


async def process_content_deletion(group_id: int, message_type: str, content: str) -> bool:
    try:
        success = False
        if message_type in ["text", "text_image", "contribute_text"]:
            if group_id in data_manager.group_texts:
                if content in data_manager.group_texts[group_id]:
                    data_manager.group_texts[group_id].remove(content)
                    success = data_manager.save_text_data(group_id)
                else:
                    found, actual_content = find_similar_text(group_id, content)
                    if found and actual_content in data_manager.group_texts[group_id]:
                        data_manager.group_texts[group_id].remove(actual_content)
                        success = data_manager.save_text_data(group_id)
        elif message_type in ["image", "contribute_image"]:
            if group_id in data_manager.group_images:
                filename = content
                if "图片投稿:" in content:
                    parts = content.split(": ")
                    if len(parts) > 1:
                        filename = parts[1].split(", ")[0]
                if filename in data_manager.group_images[group_id]:
                    data_manager.group_images[group_id].remove(filename)
                    image_dir = get_group_image_dir(group_id)
                    image_path = image_dir / filename
                    if image_path.exists():
                        image_path.unlink()
                    success = data_manager.save_image_data(group_id)

        data_manager.load_text_data(group_id)
        data_manager.load_image_data(group_id)
        return success
    except Exception as e:
        logger.error(f"删除内容时出错: {e}")
        return False


@apply_delete.handle()
async def handle_apply_delete(bot: Bot, event: GroupMessageEvent):
    try:
        if not hasattr(event, 'reply') or event.reply is None:
            await apply_delete.finish("请回复要删除的消息并说'申请删除'喵！")
        replied_message = event.reply
        group_id = event.group_id
        message_id = replied_message.message_id
        cached_message = message_cache.get_message(group_id, message_id)
        if not cached_message:
            await apply_delete.finish("该消息已超过10分钟有效期，无法申请删除喵！")
        request_id = delete_request_manager.add_request(
            group_id=group_id,
            message_id=message_id,
            requester_id=event.user_id,
            content=cached_message["content"],
            message_type=cached_message["type"]
        )
        request_info = delete_request_manager.get_request(request_id)
        await notify_superuser(bot, request_info)
        await apply_delete.finish(f"删除申请已提交 (ID: {request_id})，等待管理员处理喵！")
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        await apply_delete.finish("申请失败，请稍后重试喵！")


@handle_delete_request.handle()
async def handle_process_delete(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    try:
        arg_text = args.extract_plain_text().strip().split()
        if len(arg_text) < 2:
            await handle_delete_request.finish("使用方法: 处理删除 <申请ID> <同意/拒绝>")
        request_id = arg_text[0]
        decision = arg_text[1].lower()
        if decision not in ["同意", "拒绝", "approve", "reject"]:
            await handle_delete_request.finish("请使用'同意'或'拒绝'喵！")
        request_info = delete_request_manager.get_request(request_id)
        if not request_info:
            await handle_delete_request.finish("未找到该删除申请喵！")
        if request_info["status"] != "pending":
            await handle_delete_request.finish("该申请已被处理过了喵！")

        status = "approved" if decision in ["同意", "approve"] else "rejected"
        delete_request_manager.update_request(request_id, status, event.user_id)

        success = False
        if status == "approved":
            success = await process_content_deletion(
                request_info["group_id"],
                request_info["type"],
                request_info["content"]
            )

        result_msg = "同意" if status == "approved" else "拒绝"
        group_message = (
            f"删除申请 {request_id} 已{result_msg}处理\n"
            f"申请人: {request_info['requester_id']}\n"
        )
        if status == "approved":
            group_message += "✅ 内容已成功删除" if success else "❌ 删除失败，内容可能不存在"

        await bot.send_group_msg(group_id=request_info["group_id"], message=group_message)

        if status == "approved":
            message_cache.remove_message(request_info["group_id"], request_info["message_id"])

        await handle_delete_request.finish(f"已{result_msg}删除申请 {request_id}")
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        await handle_delete_request.finish("处理失败，请稍后重试喵！")


@view_delete_requests.handle()
async def handle_view_requests(event: PrivateMessageEvent):
    try:
        pending_requests = delete_request_manager.get_pending_requests()
        if not pending_requests:
            await view_delete_requests.finish("当前没有待处理的删除申请喵！")
        message = "📋 待处理的删除申请:\n\n"
        for i, req in enumerate(pending_requests, 1):
            content_preview = req['content']
            if req['type'] in ["image", "contribute_image"]:
                content_preview = "[图片] " + content_preview
            message += (
                f"{i}. 申请ID: {req['request_id']}\n"
                f"   群组: {req['group_id']}\n"
                f"   消息ID: {req['message_id']}\n"
                f"   申请人: {req['requester_id']}\n"
                f"   类型: {req['type']}\n"
                f"   内容: {content_preview}\n"
                f"   申请时间: {time.strftime('%m-%d %H:%M', time.localtime(req['request_time']))}\n\n"
            )
        await view_delete_requests.finish(message)
    except Exception as e:
        logger.error(f"查看删除申请时出错: {e}")
        await view_delete_requests.finish("获取申请列表失败喵！")


@clear_processed_requests.handle()
async def handle_clear_processed(event: PrivateMessageEvent):
    try:
        initial_count = len(delete_request_manager.requests_data)
        processed_ids = [req_id for req_id, data in delete_request_manager.requests_data.items() if
                         data["status"] != "pending"]
        for request_id in processed_ids:
            delete_request_manager.remove_request(request_id)
        cleared_count = len(processed_ids)
        remaining_count = initial_count - cleared_count
        await clear_processed_requests.finish(
            f"已清理 {cleared_count} 个已处理的申请，剩余 {remaining_count} 个申请喵！"
        )
    except Exception as e:
        logger.error(f"清理已处理申请时出错: {e}")
        await clear_processed_requests.finish("清理失败喵！")


# --- 文本转图片配置命令 (来自 text_to_image.py) ---
enable_text_to_image = on_command("启用文本转图片", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
disable_text_to_image = on_command("禁用文本转图片", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
text_to_image_status = on_command("文本转图片状态", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
set_text_threshold = on_command("设置文本阈值", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


@enable_text_to_image.handle()
async def handle_enable_text_to_image(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id = event.group_id
    arg_text = args.extract_plain_text().strip()
    try:
        if arg_text:
            new_threshold = int(arg_text)
            set_text_to_image_threshold(new_threshold)
            add_text_to_image_group(group_id)
            await enable_text_to_image.finish(f"已启用文本转图片功能，阈值设置为 {new_threshold} 字符喵！")
        else:
            add_text_to_image_group(group_id)
            await enable_text_to_image.finish(
                f"已启用文本转图片功能，当前阈值为 {get_text_to_image_threshold()} 字符喵！")
    except ValueError:
        await enable_text_to_image.finish("阈值必须是数字喵！")


@disable_text_to_image.handle()
async def handle_disable_text_to_image(event: GroupMessageEvent):
    group_id = event.group_id
    remove_text_to_image_group(group_id)
    await disable_text_to_image.finish("已禁用文本转图片功能喵！")


@text_to_image_status.handle()
async def handle_text_to_image_status(event: GroupMessageEvent):
    group_id = event.group_id
    enabled = is_text_to_image_enabled(group_id)
    status_msg = "启用" if enabled else "禁用"
    message = (
        f"文本转图片功能状态：{status_msg}\n"
        f"当前阈值：{get_text_to_image_threshold()} 字符\n"
        f"渲染引擎：{'htmlrender' if HTMLRENDER_AVAILABLE else 'PIL备用方案'}"
    )
    await text_to_image_status.finish(message)


@set_text_threshold.handle()
async def handle_set_text_threshold(event: GroupMessageEvent, args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()
    try:
        if not arg_text:
            await set_text_threshold.finish(f"当前文本转图片阈值为 {get_text_to_image_threshold()} 字符喵！")
            return
        new_threshold = int(arg_text)
        if new_threshold < 50:
            await set_text_threshold.finish("阈值不能小于50字符喵！")
            return
        set_text_to_image_threshold(new_threshold)
        await set_text_threshold.finish(f"已设置文本转图片阈值为 {new_threshold} 字符喵！")
    except ValueError:
        await set_text_threshold.finish("阈值必须是数字喵！")