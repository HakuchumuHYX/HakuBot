from nonebot import on_request, get_driver
from nonebot.adapters.onebot.v11 import Bot, FriendRequestEvent
from nonebot.log import logger
import time

from .config import AUTO_APPROVE_GROUPS, WELCOME_MESSAGE, REQUEST_NOTIFICATION_TEMPLATE, REJECT_MESSAGE, \
    MANUAL_REVIEW
from .data_manager import request_manager
from .utils import extract_group_candidates, create_request_data

# 注册好友请求处理器
friend_request = on_request(priority=1, block=True)

# 用于防止重复处理的缓存
processed_requests = {}
CACHE_EXPIRE_TIME = 300  # 5分钟


@friend_request.handle()
async def handle_friend_request(bot: Bot, event: FriendRequestEvent):
    """
    处理好友请求事件
    """
    user_id = event.user_id
    comment = event.comment
    flag = event.flag

    # 检查是否已经处理过这个请求
    current_time = time.time()
    request_key = f"{user_id}_{flag}"

    # 清理过期的缓存
    expired_keys = [k for k, t in processed_requests.items() if current_time - t > CACHE_EXPIRE_TIME]
    for key in expired_keys:
        del processed_requests[key]

    # 如果这个请求最近已经处理过，直接返回
    if request_key in processed_requests:
        logger.info(f"忽略已处理的好友请求: 用户{user_id}, flag: {flag}")
        return

    # 标记这个请求为已处理
    processed_requests[request_key] = current_time

    # 尝试获取请求者所在的群组（收集全部候选，避免验证信息中多个数字时误判）
    group_candidates = extract_group_candidates(comment)
    matched_groups = group_candidates & AUTO_APPROVE_GROUPS

    logger.info(f"收到好友请求: 用户{user_id}, 验证信息: {comment}, 候选群号: {group_candidates or '无'}")

    if matched_groups:
        # 候选群号与白名单有交集，自动同意
        await process_auto_approve(bot, user_id, flag)
    elif MANUAL_REVIEW:
        # 人工审核模式：记录请求并通知超管，等待 /同意好友 或 /拒绝好友 处理
        from_group = next(iter(group_candidates), None)
        await process_manual_review(bot, user_id, comment, from_group, flag)
    else:
        # 非指定群聊成员，直接拒绝并发送消息
        await process_reject_request(bot, user_id, flag)


async def process_auto_approve(bot: Bot, user_id: int, flag: str):
    """处理自动同意好友请求"""
    try:
        await bot.set_friend_add_request(flag=flag, approve=True)
        logger.info(f"已自动同意用户 {user_id} 的好友请求")

        # 可选：发送欢迎消息
        try:
            await bot.send_private_msg(user_id=user_id, message=WELCOME_MESSAGE)
        except Exception as e:
            logger.warning(f"欢迎消息发送失败: {e}")

    except Exception as e:
        logger.error(f"自动同意好友请求失败: {e}")


async def process_manual_review(bot: Bot, user_id: int, comment: str, from_group, flag: str):
    """人工审核模式：记录好友请求并通知超管手动处理"""
    # 记录到待处理列表，供 /同意好友 /拒绝好友 命令使用
    request_manager.add_request(str(user_id), create_request_data(user_id, comment, from_group, flag))

    # 格式化通知消息并逐个发送给超管
    notification = REQUEST_NOTIFICATION_TEMPLATE.format(
        user_id=user_id,
        group=from_group or "未知",
        comment=comment or "无"
    )
    superusers = get_driver().config.superusers
    if not superusers:
        logger.warning("未配置超管，无法发送好友申请通知")
        return
    for superuser in superusers:
        try:
            await bot.send_private_msg(user_id=int(superuser), message=notification)
        except Exception as e:
            logger.warning(f"向超管 {superuser} 发送好友申请通知失败: {e}")


async def process_reject_request(bot: Bot, user_id: int, flag: str):
    """处理拒绝好友请求"""
    try:
        # 拒绝好友请求
        await bot.set_friend_add_request(flag=flag, approve=False)
        logger.info(f"已拒绝用户 {user_id} 的好友请求")

        # 等待一小段时间，确保好友请求已被处理
        import asyncio
        await asyncio.sleep(1)

        # 尝试发送拒绝消息（注意：对方可能设置了不允许陌生人消息）
        try:
            await bot.send_private_msg(user_id=user_id, message=REJECT_MESSAGE)
            logger.info(f"已向用户 {user_id} 发送拒绝消息")
        except Exception as e:
            logger.warning(f"拒绝消息发送失败: {e}")

    except Exception as e:
        logger.error(f"拒绝好友请求失败: {e}")