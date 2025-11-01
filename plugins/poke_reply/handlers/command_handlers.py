# handlers/command_handlers.py
import time
import asyncio
import re
import hashlib
from typing import Tuple
from nonebot import on_command, logger, get_bot, get_driver
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    PrivateMessageEvent,
    Message,
    MessageSegment,
    Bot
)
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

from ..core.data_manager import data_manager
from ..managers.cache_manager import message_cache
from ..managers.delete_request_manager import delete_request_manager
from ..config import get_group_image_dir
from ..utils.common import download_and_hash_image

# 注册命令处理器
apply_delete = on_command("申请删除", rule=to_me(), priority=5, block=True)
handle_delete_request = on_command("处理删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_delete_requests = on_command("查看删除申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
clear_processed_requests = on_command("清理已处理申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)

async def get_image_preview(group_id: int, content: str, message_type: str) -> Tuple[bool, str]:
    """
    获取图片预览

    Args:
        group_id: 群组ID
        content: 内容（可能是文件名或描述）
        message_type: 消息类型

    Returns:
        Tuple[bool, str]: (是否成功, 图片路径或错误信息)
    """
    try:
        if message_type == "image":
            # 直接是图片文件名
            image_dir = get_group_image_dir(group_id)
            image_path = image_dir / content

            if image_path.exists():
                return True, str(image_path)
            else:
                return False, f"图片文件不存在: {content}"

        elif message_type == "contribute_image":
            # 投稿图片，格式为 "图片投稿: 文件名1, 文件名2, ..."
            if "图片投稿:" in content:
                # 提取文件名部分
                parts = content.split(": ")
                if len(parts) > 1:
                    # 取第一个文件名
                    filename = parts[1].split(", ")[0]
                    image_dir = get_group_image_dir(group_id)
                    image_path = image_dir / filename

                    if image_path.exists():
                        return True, str(image_path)
                    else:
                        return False, f"投稿图片文件不存在: {filename}"

        return False, "不支持的消息类型或格式错误"
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"获取图片预览失败: {e}")
        return False, f"获取图片预览失败: {str(e)}"


async def notify_superuser(bot: Bot, request_info: dict):
    """通知超级用户有新的删除申请"""
    try:
        # 获取所有超级用户
        superusers = list(get_driver().config.superusers)

        # 构建基础消息
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

        # 如果是图片类型，尝试获取图片预览
        image_preview_sent = False
        if request_info['type'] in ["image", "contribute_image"]:
            success, image_path_or_error = await get_image_preview(
                request_info['group_id'],
                request_info['content'],
                request_info['type']
            )

            if success:
                # 发送图片预览
                image_message = base_message + f"\n\n图片预览:"
                for superuser in superusers:
                    try:
                        # 先发送文本消息
                        await bot.send_private_msg(
                            user_id=int(superuser),
                            message=image_message
                        )
                        # 再发送图片
                        await bot.send_private_msg(
                            user_id=int(superuser),
                            message=Message(MessageSegment.image(f"file:///{image_path_or_error}"))
                        )
                        image_preview_sent = True

                    except Exception as e:
                        logger.error(f"向超级用户 {superuser} 发送图片预览失败: {e}")
                        # 如果发送图片失败，回退到文本消息
                        await bot.send_private_msg(
                            user_id=int(superuser),
                            message=base_message + f"\n\n图片预览发送失败: {str(e)}"
                        )
            else:
                # 图片预览获取失败，发送错误信息
                for superuser in superusers:
                    await bot.send_private_msg(
                        user_id=int(superuser),
                        message=base_message + f"\n\n{image_path_or_error}"
                    )

        # 如果不是图片类型，或者图片预览发送失败，发送基础消息
        if not image_preview_sent and request_info['type'] not in ["image", "contribute_image"]:
            # 对于文本类型，添加内容预览
            content_preview = request_info['content'][:100] + "..." if len(request_info['content']) > 100 else \
            request_info['content']
            final_message = base_message + f"\n\n内容预览: {content_preview}"

            for superuser in superusers:
                await bot.send_private_msg(user_id=int(superuser), message=final_message)

    except Exception as e:
        logger.error(f"通知超级用户失败: {e}")


def find_similar_text(group_id: int, target_content: str, threshold: float = 0.9) -> Tuple[bool, str]:
    """
    在文本列表中查找相似文本

    Args:
        group_id: 群组ID
        target_content: 目标文本内容
        threshold: 相似度阈值

    Returns:
        Tuple[bool, str]: (是否找到, 找到的文本内容)
    """
    try:
        if group_id not in data_manager.group_texts:
            return False, ""

        # 预处理目标文本
        def preprocess_text(text):
            # 移除标点符号和空格，转换为小写
            text = re.sub(r'[^\w]', '', text)
            return text.lower()

        target_processed = preprocess_text(target_content)

        for text in data_manager.group_texts[group_id]:
            text_processed = preprocess_text(text)

            # 计算相似度（简单的字符重叠比例）
            if len(target_processed) == 0 or len(text_processed) == 0:
                continue

            # 计算Jaccard相似度
            set_target = set(target_processed)
            set_text = set(text_processed)

            intersection = len(set_target & set_text)
            union = len(set_target | set_text)

            similarity = intersection / union if union > 0 else 0

            if similarity >= threshold:
                logger.info(f"找到相似文本: 相似度={similarity:.2f}")
                return True, text

        return False, ""

    except Exception as e:
        logger.error(f"查找相似文本时出错: {e}")
        return False, ""


async def process_content_deletion(group_id: int, message_type: str, content: str) -> bool:
    """
    处理内容删除

    Returns:
        bool: 是否删除成功
    """
    try:
        success = False

        if message_type in ["text", "text_image", "contribute_text"]:
            # 从文本数据中删除
            if group_id in data_manager.group_texts:
                # 首先尝试精确匹配
                if content in data_manager.group_texts[group_id]:
                    data_manager.group_texts[group_id].remove(content)
                    success = data_manager.save_text_data(group_id)
                    logger.info(f"删除文本内容（精确匹配）: 群组={group_id}, 内容长度={len(content)}")
                else:
                    # 如果精确匹配失败，尝试相似度匹配（针对长文本转图片的情况）
                    logger.info(f"精确匹配失败，尝试相似度匹配: 群组={group_id}, 内容长度={len(content)}")
                    found, actual_content = find_similar_text(group_id, content)

                    if found and actual_content in data_manager.group_texts[group_id]:
                        data_manager.group_texts[group_id].remove(actual_content)
                        success = data_manager.save_text_data(group_id)
                        logger.info(f"删除文本内容（相似度匹配）: 群组={group_id}, 内容长度={len(actual_content)}")
                    else:
                        logger.error(f"文本内容不存在: 群组={group_id}, 内容长度={len(content)}")

        elif message_type in ["image", "contribute_image"]:
            # 从图片数据中删除
            if group_id in data_manager.group_images:
                # content 是图片文件名或包含文件名的描述
                # 尝试从描述中提取文件名
                filename = content
                if "图片投稿:" in content:
                    # 提取文件名部分
                    parts = content.split(": ")
                    if len(parts) > 1:
                        filename = parts[1].split(", ")[0]  # 取第一个文件名

                if filename in data_manager.group_images[group_id]:
                    data_manager.group_images[group_id].remove(filename)
                    # 删除图片文件
                    image_dir = get_group_image_dir(group_id)
                    image_path = image_dir / filename
                    if image_path.exists():
                        image_path.unlink()
                        logger.info(f"删除图片文件: {filename}")
                    success = data_manager.save_image_data(group_id)
                else:
                    logger.error(f"图片文件不存在: {filename}")

        # 重新加载数据
        data_manager.load_text_data(group_id)
        data_manager.load_image_data(group_id)
        logger.info(f"数据重载完成: 群组={group_id}")

        return success

    except Exception as e:
        logger.error(f"删除内容时出错: {e}")
        return False


@apply_delete.handle()
async def handle_apply_delete(bot: Bot, event: GroupMessageEvent):
    """处理删除申请"""
    try:
        # 检查是否是回复消息
        if not hasattr(event, 'reply') or event.reply is None:
            await apply_delete.finish("请回复要删除的消息并说'申请删除'喵！")
            return

        # 获取被回复的消息
        replied_message = event.reply
        group_id = event.group_id
        message_id = replied_message.message_id

        logger.info(f"收到删除申请: 群组={group_id}, 消息ID={message_id}, 申请人={event.user_id}")

        # 查找消息缓存
        cached_message = message_cache.get_message(group_id, message_id)

        if not cached_message:
            await apply_delete.finish("该消息已超过10分钟有效期，无法申请删除喵！")
            return

        # 添加删除申请
        request_id = delete_request_manager.add_request(
            group_id=group_id,
            message_id=message_id,
            requester_id=event.user_id,
            content=cached_message["content"],
            message_type=cached_message["type"]
        )

        # 通知超级用户
        request_info = delete_request_manager.get_request(request_id)
        await notify_superuser(bot, request_info)

        # 使用 finish 结束处理，发送成功消息
        await apply_delete.finish(f"删除申请已提交 (ID: {request_id})，等待管理员处理喵！")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        # 只有在真正出错时才发送错误消息
        await apply_delete.finish("申请失败，请稍后重试喵！")


@handle_delete_request.handle()
async def handle_process_delete(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """处理删除申请（超级用户）"""
    try:
        arg_text = args.extract_plain_text().strip().split()
        if len(arg_text) < 2:
            await handle_delete_request.finish("使用方法: 处理删除 <申请ID> <同意/拒绝>")
            return

        request_id = arg_text[0]
        decision = arg_text[1].lower()

        if decision not in ["同意", "拒绝", "approve", "reject"]:
            await handle_delete_request.finish("请使用'同意'或'拒绝'喵！")
            return

        # 获取申请信息
        request_info = delete_request_manager.get_request(request_id)
        if not request_info:
            await handle_delete_request.finish("未找到该删除申请喵！")
            return

        if request_info["status"] != "pending":
            await handle_delete_request.finish("该申请已被处理过了喵！")
            return

        # 更新申请状态
        status = "approved" if decision in ["同意", "approve"] else "rejected"
        delete_request_manager.update_request(request_id, status, event.user_id)

        # 如果同意删除，执行删除操作
        success = False
        if status == "approved":
            success = await process_content_deletion(
                request_info["group_id"],
                request_info["type"],
                request_info["content"]
            )

        # 通知群组结果
        result_msg = "同意" if status == "approved" else "拒绝"
        group_message = (
            f"删除申请 {request_id} 已{result_msg}处理\n"
            f"申请人: {request_info['requester_id']}\n"
        )

        if status == "approved":
            if success:
                group_message += "✅ 内容已成功删除，数据已重载"
            else:
                group_message += "❌ 删除失败，内容可能不存在"

        await bot.send_group_msg(
            group_id=request_info["group_id"],
            message=group_message
        )

        # 如果处理完成，从缓存中移除对应的消息
        if status == "approved":
            message_cache.remove_message(request_info["group_id"], request_info["message_id"])

        # 给管理员发送私聊确认消息
        await handle_delete_request.finish(f"已{result_msg}删除申请 {request_id}")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        await handle_delete_request.finish("处理失败，请稍后重试喵！")


@view_delete_requests.handle()
async def handle_view_requests(event: PrivateMessageEvent):
    """查看待处理的删除申请（超级用户）"""
    try:
        pending_requests = delete_request_manager.get_pending_requests()

        if not pending_requests:
            await view_delete_requests.finish("当前没有待处理的删除申请喵！")
            return

        message = "📋 待处理的删除申请:\n\n"
        for i, req in enumerate(pending_requests, 1):
            # 对于图片类型，显示图片预览标记
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
        
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"查看删除申请时出错: {e}")
        await view_delete_requests.finish("获取申请列表失败喵！")


@clear_processed_requests.handle()
async def handle_clear_processed(event: PrivateMessageEvent):
    """清理已处理的删除申请"""
    try:
        initial_count = len(delete_request_manager.requests_data)

        # 找出已处理的申请
        processed_ids = []
        for request_id, request_data in delete_request_manager.requests_data.items():
            if request_data["status"] != "pending":
                processed_ids.append(request_id)

        # 移除已处理的申请
        for request_id in processed_ids:
            delete_request_manager.remove_request(request_id)

        cleared_count = len(processed_ids)
        remaining_count = initial_count - cleared_count

        await clear_processed_requests.finish(
            f"已清理 {cleared_count} 个已处理的申请，剩余 {remaining_count} 个申请喵！"
        )
        
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"清理已处理申请时出错: {e}")
        await clear_processed_requests.finish("清理失败喵！")