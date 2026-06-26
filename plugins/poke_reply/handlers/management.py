import time
from typing import Tuple, Union
from nonebot import on_command, on_message, logger, get_driver
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment, Bot
)
from nonebot.rule import to_me, is_type
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

from ..config import (
    get_group_image_dir, add_text_to_image_group, remove_text_to_image_group,
    is_text_to_image_enabled, set_text_to_image_threshold, get_text_to_image_threshold
)
from ..models.data import data_manager
from ..models.cache import message_cache
from ..models.request import delete_request_manager
from ..services.image import invalidate_cache_for_file
from ..services.text import HTMLRENDER_AVAILABLE
from plugins.utils.image_utils import path_to_base64_image

# --- 注册命令 ---
apply_delete = on_command("申请删除", rule=to_me(), priority=5, block=True)
su_direct_delete = on_command("poke删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
handle_delete_request = on_command("处理删除", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_delete_requests = on_command("查看删除申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
clear_processed_requests = on_command("清理已处理申请", permission=SUPERUSER, rule=to_me(), priority=5, block=True)

# 私聊回复监听器
private_reply_handler = on_message(
    rule=to_me() & is_type(PrivateMessageEvent),
    permission=SUPERUSER,
    priority=10,
    block=False
)

enable_text_to_image = on_command("启用文本转图片", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
disable_text_to_image = on_command("禁用文本转图片", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
text_to_image_status = on_command("文本转图片状态", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
set_text_threshold = on_command("设置文本阈值", permission=SUPERUSER, rule=to_me(), priority=5, block=True)

# --- 辅助逻辑 ---

def _extract_image_filenames(content: str, message_type: str, filenames=None) -> list:
    if filenames:
        return [str(filename).strip() for filename in filenames if str(filename).strip()]
    if message_type == "image":
        return [content] if content else []
    if message_type == "contribute_image" and "图片投稿:" in content:
        _, raw_names = content.split(":", 1)
        return [name.strip() for name in raw_names.split(",") if name.strip()]
    return []


async def get_image_preview(group_id: int, content: str, message_type: str, filenames=None) -> Tuple[bool, str]:
    try:
        image_filenames = _extract_image_filenames(content, message_type, filenames)
        if image_filenames:
            image_dir = get_group_image_dir(group_id)
            filename = image_filenames[0]
            image_path = image_dir / filename
            return (True, str(image_path)) if image_path.exists() else (False, f"图片文件不存在: {filename}")
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
            f"请回复 '同意' 或 '拒绝'，或使用命令:\n"
            f"处理删除 {request_info['request_id']} 同意/拒绝"
        )

        image_preview_sent = False
        sent_msg_ids = []

        if request_info['type'] in ["image", "contribute_image"]:
            success, image_path_or_error = await get_image_preview(
                request_info['group_id'],
                request_info['content'],
                request_info['type'],
                request_info.get("filenames"),
            )
            if success:
                for superuser in superusers:
                    try:
                        # 发送文本部分
                        text_receipt = await bot.send_private_msg(user_id=int(superuser), message=base_message + f"\n\n图片预览:")
                        sent_msg_ids.append(text_receipt['message_id'])
                        
                        # 发送图片部分
                        img_receipt = await bot.send_private_msg(user_id=int(superuser), message=Message(path_to_base64_image(image_path_or_error)))
                        sent_msg_ids.append(img_receipt['message_id'])
                        
                        image_preview_sent = True
                    except Exception as e:
                        logger.error(f"向超级用户 {superuser} 发送图片预览失败: {e}")
            else:
                for superuser in superusers:
                    receipt = await bot.send_private_msg(user_id=int(superuser), message=base_message + f"\n\n{image_path_or_error}")
                    sent_msg_ids.append(receipt['message_id'])
        
        if not image_preview_sent and request_info['type'] not in ["image", "contribute_image"]:
            preview_source = request_info.get("content_preview", request_info['content'])
            content_preview = preview_source[:100] + "..." if len(preview_source) > 100 else preview_source
            final_message = base_message + f"\n\n内容预览: {content_preview}"
            for superuser in superusers:
                receipt = await bot.send_private_msg(user_id=int(superuser), message=final_message)
                sent_msg_ids.append(receipt['message_id'])
        
        # 记录通知ID与申请ID的映射
        for mid in sent_msg_ids:
            delete_request_manager.add_notification_map(mid, request_info['request_id'])
            
    except Exception as e:
        logger.error(f"通知超级用户失败: {e}")

async def process_content_deletion(group_id: int, message_type: str, content: str, filenames=None) -> bool:
    try:
        success = False
        if message_type in ["text", "text_image", "contribute_text"]:
            success = data_manager.remove_text(group_id, content)
        elif message_type in ["image", "contribute_image"]:
            target_filenames = _extract_image_filenames(content, message_type, filenames)
            if target_filenames:
                success = True
                image_dir = get_group_image_dir(group_id)
                for filename in target_filenames:
                    image_path = image_dir / filename
                    if image_path.exists():
                        invalidate_cache_for_file(image_path)
                    if not data_manager.remove_image(group_id, filename):
                        success = False
        return success
    except Exception as e:
        logger.error(f"删除内容时出错: {e}")
        return False

# --- 删除相关Handler ---

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
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        await apply_delete.finish("申请失败，请稍后重试喵！")

@su_direct_delete.handle()
async def handle_su_direct_delete(bot: Bot, event: GroupMessageEvent):
    """
    SU 直接回复 '删除' 时的处理逻辑。
    无需申请，直接执行删除操作。
    """
    try:
        if not hasattr(event, 'reply') or event.reply is None:
            await su_direct_delete.finish("请回复要删除的消息并说'删除'喵！")
        
        replied_message = event.reply
        group_id = event.group_id
        message_id = replied_message.message_id
        
        # 尝试从缓存获取消息内容
        cached_message = message_cache.get_message(group_id, message_id)
        
        if not cached_message:
            await su_direct_delete.finish("该消息已过期或未被缓存，无法自动删除喵！")

        # 执行删除
        success = await process_content_deletion(
            group_id,
            cached_message["type"],
            cached_message["content"]
        )
        
        if success:
            message_cache.remove_message(group_id, message_id)
            await su_direct_delete.finish("✅ 内容已成功删除！")
        else:
            await su_direct_delete.finish("❌ 删除失败，内容可能不存在或文件已被移除。")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"SU直接删除出错: {e}")
        await su_direct_delete.finish(f"执行删除时出错: {e}")

async def execute_delete_decision(bot: Bot, request_id: str, decision: str, processor_id: int) -> str:
    """
    执行删除申请的审批逻辑（提取为公共函数供命令和私聊回复使用）
    """
    request_info = delete_request_manager.get_request(request_id)
    if not request_info:
        return "未找到该删除申请喵！"
    if request_info["status"] != "pending":
        return "该申请已被处理过了喵！"
    
    status = "approved" if decision in ["同意", "approve"] else "rejected"
    delete_request_manager.update_request(request_id, status, processor_id)
    
    success = False
    if status == "approved":
        success = await process_content_deletion(
            request_info["group_id"],
            request_info["type"],
            request_info["content"],
            request_info.get("filenames"),
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
        
    return f"已{result_msg}删除申请 {request_id}"

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
            
        result = await execute_delete_decision(bot, request_id, decision, event.user_id)
        await handle_delete_request.finish(result)
        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理删除申请时出错: {e}")
        await handle_delete_request.finish("处理失败，请稍后重试喵！")

@private_reply_handler.handle()
async def handle_private_reply(bot: Bot, event: PrivateMessageEvent):
    """
    处理管理员在私聊中对删除申请通知的回复
    """
    try:
        # 检查是否是回复消息
        if not event.reply:
            return

        # 检查回复的内容是否是有效的指令
        msg = event.get_plaintext().strip().lower()
        if msg not in ["同意", "拒绝", "approve", "reject"]:
            return

        # 检查回复的消息是否关联了某个申请
        replied_msg_id = event.reply.message_id
        request_id = delete_request_manager.get_request_id_by_notification(replied_msg_id)
        
        if not request_id:
            return

        # 执行处理
        result = await execute_delete_decision(bot, request_id, msg, event.user_id)
        await private_reply_handler.finish(result)

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理私聊回复出错: {e}")
        # 私聊回复出错不一定要finish，因为可能是普通聊天
        pass

@view_delete_requests.handle()
async def handle_view_requests(event: PrivateMessageEvent):
    try:
        pending_requests = delete_request_manager.get_pending_requests()
        if not pending_requests:
            await view_delete_requests.finish("当前没有待处理的删除申请喵！")
        message = "📋 待处理的删除申请:\n\n"
        for i, req in enumerate(pending_requests, 1):
            content_preview = req.get('content_preview', req['content'])
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
        raise
    except Exception as e:
        logger.error(f"查看删除申请时出错: {e}")
        await view_delete_requests.finish("获取申请列表失败喵！")

@clear_processed_requests.handle()
async def handle_clear_processed(event: PrivateMessageEvent):
    try:
        initial_count = len(delete_request_manager.requests_data)
        processed_ids = [req_id for req_id, data in delete_request_manager.requests_data.items() if data["status"] != "pending"]
        for request_id in processed_ids:
            delete_request_manager.remove_request(request_id)
        cleared_count = len(processed_ids)
        remaining_count = initial_count - cleared_count
        await clear_processed_requests.finish(f"已清理 {cleared_count} 个已处理的申请，剩余 {remaining_count} 个申请喵！")
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"清理已处理申请时出错: {e}")
        await clear_processed_requests.finish("清理失败喵！")

# --- 文本转图片配置 Handler ---

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
            await enable_text_to_image.finish(f"已启用文本转图片功能，当前阈值为 {get_text_to_image_threshold()} 字符喵！")
    except FinishedException:
        raise
    except ValueError:
        await enable_text_to_image.finish("阈值必须是数字喵！")

@disable_text_to_image.handle()
async def handle_disable_text_to_image(event: GroupMessageEvent):
    try:
        group_id = event.group_id
        remove_text_to_image_group(group_id)
        await disable_text_to_image.finish("已禁用文本转图片功能喵！")
    except FinishedException:
        raise

@text_to_image_status.handle()
async def handle_text_to_image_status(event: GroupMessageEvent):
    try:
        group_id = event.group_id
        enabled = is_text_to_image_enabled(group_id)
        status_msg = "启用" if enabled else "禁用"
        message = (
            f"文本转图片功能状态：{status_msg}\n"
            f"当前阈值：{get_text_to_image_threshold()} 字符\n"
            f"渲染引擎：{'htmlrender' if HTMLRENDER_AVAILABLE else 'PIL备用方案'}"
        )
        await text_to_image_status.finish(message)
    except FinishedException:
        raise

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
    except FinishedException:
        raise
    except ValueError:
        await set_text_threshold.finish("阈值必须是数字喵！")
