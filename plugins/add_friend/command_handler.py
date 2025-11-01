from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .config import COMMAND_PRIORITY, FRIEND_APPROVED_MESSAGE
from .data_manager import request_manager

# SUPERUSER 处理好友请求的命令
approve_friend = on_command("同意好友", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)
reject_friend = on_command("拒绝好友", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)

@approve_friend.handle()
async def approve_friend_handler(matcher: Matcher, bot: Bot, args: Message = CommandArg()):
    """处理同意好友命令"""
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await matcher.finish("请提供要同意的好友QQ号，格式：/同意好友 [QQ号]")

    # 记录当前所有待处理请求，用于调试
    logger.info(f"当前待处理请求: {request_manager.get_all_pending_requests()}")

    if not request_manager.has_pending_request(user_id_str):
        await matcher.finish(f"未找到QQ号 {user_id_str} 的好友申请记录。请确认QQ号是否正确，或该申请已被处理。")

    try:
        request_data = request_manager.get_request(user_id_str)
        # 同意好友请求
        await bot.set_friend_add_request(flag=request_data["flag"], approve=True)

        # 发送同意后的消息（可选）
        try:
            await bot.send_private_msg(
                user_id=request_data["user_id"],
                message=FRIEND_APPROVED_MESSAGE
            )
        except Exception:
            logger.warning("无法发送欢迎消息给新好友")

        # 清理记录
        request_manager.remove_request(user_id_str)

        # 直接发送成功消息，不使用 matcher.finish
        await matcher.send(f"已同意 {user_id_str} 的好友申请")

    except Exception as e:
        logger.error(f"同意好友请求时出错: {e}")
        await matcher.send(f"处理失败：{str(e)}")

@reject_friend.handle()
async def reject_friend_handler(matcher: Matcher, bot: Bot, args: Message = CommandArg()):
    """处理拒绝好友命令"""
    user_id_str = args.extract_plain_text().strip()
    if not user_id_str:
        await matcher.finish("请提供要拒绝的好友QQ号，格式：/拒绝好友 [QQ号]")

    # 记录当前所有待处理请求，用于调试
    logger.info(f"当前待处理请求: {request_manager.get_all_pending_requests()}")

    if not request_manager.has_pending_request(user_id_str):
        await matcher.finish(f"未找到QQ号 {user_id_str} 的好友申请记录。请确认QQ号是否正确，或该申请已被处理。")

    try:
        request_data = request_manager.get_request(user_id_str)
        # 拒绝好友请求
        await bot.set_friend_add_request(flag=request_data["flag"], approve=False)

        # 清理记录
        request_manager.remove_request(user_id_str)

        # 直接发送成功消息，不使用 matcher.finish
        await matcher.send(f"已拒绝 {user_id_str} 的好友申请")

    except Exception as e:
        logger.error(f"拒绝好友请求时出错: {e}")
        await matcher.send(f"处理失败：{str(e)}")