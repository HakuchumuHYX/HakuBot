import logging
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

# 导入共享上下文
from .content import message_context

# 获取配置中的超级用户列表
superusers = get_driver().config.superusers


async def get_user_info(bot: Bot, user_id: str) -> str:
    """获取用户信息"""
    try:
        user_info = await bot.get_stranger_info(user_id=int(user_id))
        return f"{user_info['nickname']}({user_id})"
    except Exception as e:
        logger.warning(f"获取用户 {user_id} 信息失败: {e}")
        return f"用户{user_id}"


reply_message = on_message(
    rule=lambda event: isinstance(event, PrivateMessageEvent) and event.get_user_id() in superusers,
    permission=SUPERUSER,
    priority=1,
    block=False
)


@reply_message.handle()
async def handle_reply_message(bot: Bot, event: PrivateMessageEvent):
    """处理超级用户直接回复的消息"""

    logger.info(f"收到超级用户 {event.get_user_id()} 的消息，检查是否为回复消息")

    # 检查是否是回复消息
    if not event.reply:
        logger.info("不是回复消息，跳过处理")
        return

    replied_msg_id = event.reply.message_id
    logger.info(f"检测到回复消息，被回复的消息ID: {replied_msg_id}")

    # 检查回复的消息是否在我们的上下文中
    if replied_msg_id not in message_context:
        logger.warning(f"回复的消息ID {replied_msg_id} 不在上下文中，可用的上下文键: {list(message_context.keys())}")
        return

    context = message_context[replied_msg_id]
    user_id = context["user_id"]
    user_info = context["user_info"]
    superuser_id = event.get_user_id()
    superuser_info = await get_user_info(bot, superuser_id)

    logger.info(f"找到消息上下文: 用户 {user_id}, 原消息来自 {'群聊' if 'group_id' in context else '私聊'}")

    # 获取回复内容
    reply_content = event.get_plaintext().strip()

    if not reply_content:
        await reply_message.finish("回复内容不能为空")

    # 构建回复消息
    reply_message_content = f"\nSU回复了你的消息：\n{reply_content}"

    try:
        # 如果原消息来自群聊，发送到群聊
        if "group_id" in context:
            group_id = int(context["group_id"])
            group_info = context["group_info"]
            # 添加@用户
            at_segment = MessageSegment.at(user_id)
            full_message = at_segment + MessageSegment.text(f" {reply_message_content}")
            await bot.send_group_msg(group_id=group_id, message=full_message)
            logger.info(f"超级用户 {superuser_id} 在群 {group_id} 中回复用户 {user_id}")
            await reply_message.finish(f"回复已发送到群 {group_info}")
        else:
            # 发送私聊回复
            await bot.send_private_msg(
                user_id=int(user_id),
                message=reply_message_content
            )
            logger.info(f"超级用户 {superuser_id} 成功私聊回复用户 {user_id}")
            await reply_message.finish(f"回复已发送给 {user_info}")
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"发送回复失败: {e}", exc_info=True)
        await reply_message.finish(f"回复发送失败: {e}")
