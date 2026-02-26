from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .config import COMMAND_PRIORITY, FRIEND_APPROVED_MESSAGE, AUTO_APPROVE_GROUPS, load_auto_approve_groups, \
    save_auto_approve_groups
from .data_manager import request_manager

# SUPERUSER 处理好友请求的命令
approve_friend = on_command("同意好友", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)
reject_friend = on_command("拒绝好友", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)
# 配置管理命令
list_groups = on_command("查看白名单群组", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)
add_group = on_command("添加白名单群组", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)
remove_group = on_command("移除白名单群组", permission=SUPERUSER, priority=COMMAND_PRIORITY, block=True)


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


@list_groups.handle()
async def list_groups_handler(matcher: Matcher):
    """查看白名单群组"""
    groups = AUTO_APPROVE_GROUPS
    if groups:
        group_list = "\n".join([f"- {group}" for group in sorted(groups)])
        await matcher.send(f"当前白名单群组 ({len(groups)} 个):\n{group_list}")
    else:
        await matcher.send("当前没有配置白名单群组")


@add_group.handle()
async def add_group_handler(matcher: Matcher, args: Message = CommandArg()):
    """添加白名单群组"""
    group_id = args.extract_plain_text().strip()
    if not group_id:
        await matcher.finish("请提供要添加的群号，格式：/添加白名单群组 [群号]")

    if not group_id.isdigit():
        await matcher.finish("群号必须为数字")

    # 重新加载当前配置
    current_groups = load_auto_approve_groups()
    if group_id in current_groups:
        await matcher.finish(f"群组 {group_id} 已在白名单中")

    current_groups.add(group_id)
    if save_auto_approve_groups(current_groups):
        # 更新内存中的配置
        from .config import AUTO_APPROVE_GROUPS
        AUTO_APPROVE_GROUPS.clear()
        AUTO_APPROVE_GROUPS.update(current_groups)
        await matcher.send(f"已添加群组 {group_id} 到白名单")
    else:
        await matcher.send("添加失败，请检查日志")


@remove_group.handle()
async def remove_group_handler(matcher: Matcher, args: Message = CommandArg()):
    """移除白名单群组"""
    group_id = args.extract_plain_text().strip()
    if not group_id:
        await matcher.finish("请提供要移除的群号，格式：/移除白名单群组 [群号]")

    # 重新加载当前配置
    current_groups = load_auto_approve_groups()
    if group_id not in current_groups:
        await matcher.finish(f"群组 {group_id} 不在白名单中")

    current_groups.remove(group_id)
    if save_auto_approve_groups(current_groups):
        # 更新内存中的配置
        from .config import AUTO_APPROVE_GROUPS
        AUTO_APPROVE_GROUPS.clear()
        AUTO_APPROVE_GROUPS.update(current_groups)
        await matcher.send(f"已从白名单移除群组 {group_id}")
    else:
        await matcher.send("移除失败，请检查日志")