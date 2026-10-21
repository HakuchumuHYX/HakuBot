import time
from nonebot import on_command, get_driver, logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
)
from nonebot.params import CommandArg
from nonebot.rule import to_me

from utils.logging import get_exc_desc
from utils.onebot.rules import command_boundary

# 导入共享上下文
from plugins.send_and_reply.content import message_context, prune_message_context
from core.access import switch_rule

# 获取配置中的超级用户列表
superusers = get_driver().config.superusers

# 创建命令处理器，需要@机器人触发
send_to_superuser = on_command(
    "send",
    rule=to_me() & command_boundary & switch_rule("send_and_reply"),
    priority=5,
    block=True,
)


async def get_user_info(bot: Bot, user_id: str) -> str:
    """获取用户信息"""
    try:
        user_info = await bot.get_stranger_info(user_id=int(user_id))
        return f"{user_info['nickname']}({user_id})"
    except Exception as e:
        logger.warning(f"获取用户 {user_id} 信息失败: {get_exc_desc(e)}")
        return f"用户{user_id}"


async def get_group_info(bot: Bot, group_id: str) -> str:
    """获取群组信息"""
    try:
        group_info = await bot.get_group_info(group_id=int(group_id))
        return f"{group_info['group_name']}({group_id})"
    except Exception as e:
        logger.warning(f"获取群组 {group_id} 信息失败: {get_exc_desc(e)}")
        return f"群组{group_id}"


@send_to_superuser.handle()
async def handle_send_command(bot: Bot, event: Event, arg: Message = CommandArg()):
    """处理send命令"""
    user_id = str(event.user_id)

    # 额外检查：确保消息是@机器人的
    if isinstance(event, GroupMessageEvent):
        original_message = event.original_message
        at_segments = [
            seg
            for seg in original_message
            if seg.type == "at" and seg.data.get("qq") == str(bot.self_id)
        ]

        if not at_segments:
            return

    # 获取消息文本
    text = arg.extract_plain_text().strip()

    if not text:
        await send_to_superuser.finish(
            "请提供要发送的文本内容，格式：@机器人 send 你要发送的内容"
        )

    # 获取发送者的用户ID
    user_id = event.get_user_id()
    user_info = await get_user_info(bot, user_id)

    # 构建上下文信息
    context_info = {
        "user_id": user_id,
        "user_info": user_info,
        "timestamp": time.time(),
    }

    # 如果是群消息，添加群信息
    if isinstance(event, GroupMessageEvent):
        try:
            group_info = await get_group_info(bot, str(event.group_id))
            context_info["group_id"] = str(event.group_id)
            context_info["group_info"] = group_info
            message_header = f"来自群聊 {group_info} 的用户 {user_info} 的消息："
        except Exception as e:
            logger.opt(exception=True).warning(f"构建群消息头失败: {e}")
            context_info["group_id"] = str(event.group_id)
            context_info["group_info"] = f"群组{event.group_id}"
            message_header = f"来自群聊的用户 {user_info} 的消息："
    else:
        message_header = f"来自私聊用户 {user_info} 的消息："

    # 构建完整的消息内容 - 恢复回复提示
    full_message = f"{message_header}\n\n{text}\n\n请直接回复此消息进行回复"

    # 记录消息发送尝试
    logger.debug(f"用户 {user_id} 尝试发送消息给超级用户: {text[:50]}...")

    # 存入新条目前先清理过期上下文，防止内存泄漏
    prune_message_context()

    # 发送给所有超级用户
    success_count = 0
    failed_users = []

    for superuser_id in superusers:
        try:
            superuser_id_int = int(superuser_id)
            result = await bot.send_private_msg(
                user_id=superuser_id_int, message=full_message
            )

            # 存储消息上下文，用于回复功能
            if hasattr(result, "message_id"):
                message_id = result.message_id
                message_context[message_id] = context_info
                logger.debug(
                    f"存储消息上下文成功，消息ID: {message_id}, 用户ID: {user_id}"
                )
            elif isinstance(result, dict) and "message_id" in result:
                message_id = result["message_id"]
                message_context[message_id] = context_info
                logger.debug(
                    f"存储消息上下文成功，消息ID: {message_id}, 用户ID: {user_id}"
                )
            else:
                logger.warning(f"无法获取消息ID，结果类型: {type(result)}")

            success_count += 1
            logger.debug(f"消息成功发送给超级用户 {superuser_id}")
        except Exception as e:
            error_msg = f"发送消息给超级用户 {superuser_id} 失败: {e}"
            logger.warning(f"发送消息给超级用户 {superuser_id} 失败: {get_exc_desc(e)}")
            failed_users.append((superuser_id, error_msg))

    # 记录发送结果
    if success_count > 0:
        result_msg = f"消息已发送给 {success_count} 位超级用户"
        logger.info(f"消息发送成功: {result_msg}")
        await send_to_superuser.finish(f"消息已发送，请静待回复~")
    else:
        error_msg = "所有消息发送尝试均失败"
        logger.error(error_msg)
        await send_to_superuser.finish("消息发送失败，请稍后再试~")
