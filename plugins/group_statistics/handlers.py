from nonebot import on_message, on_command, on
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, Message, Event, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

# 导入管理模块
from ..plugin_manager.enable import is_plugin_enabled

from .data_manager import data_manager
from .utils import get_total_messages, get_top_users
from .render import render_today_stat_image
from .config import MESSAGE_HANDLER_PRIORITY, STAT_COMMAND_PRIORITY

# 创建消息处理器
message_handler = on_message(priority=MESSAGE_HANDLER_PRIORITY, block=False)


@message_handler.handle()
async def handle_group_message(event: GroupMessageEvent):
    """处理群消息并更新统计"""
    group_id = event.group_id

    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id), "0"):
        return

    user_id = event.user_id
    user_card = event.sender.card or event.sender.nickname

    # 记录用户消息
    data_manager.record_user_message(group_id, user_id, user_card)


# 监听自身发送的消息 (OneBot 扩展事件 message_sent)
def is_message_sent(event: Event) -> bool:
    return getattr(event, "post_type", "") == "message_sent"

sent_handler = on(rule=is_message_sent, priority=MESSAGE_HANDLER_PRIORITY, block=False)


@sent_handler.handle()
async def handle_sent_message(bot: Bot, event: Event):
    """处理自身发送的消息并更新统计"""
    # 确保是群消息
    if getattr(event, "message_type", "") != "group":
        return

    group_id = getattr(event, "group_id", None)
    if not group_id:
        return

    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id), "0"):
        return

    # 尝试获取用户ID，如果获取失败则使用机器人自身ID
    user_id = getattr(event, "user_id", bot.self_id)
    
    # 尝试获取发送者名片
    user_card = "ATRI"
    if hasattr(event, "sender"):
        sender = event.sender
        if isinstance(sender, dict):
            user_card = sender.get("card") or sender.get("nickname") or "ATRI"
        else:
            user_card = getattr(sender, "card", "") or getattr(sender, "nickname", "") or "ATRI"

    # 记录消息
    data_manager.record_user_message(group_id, int(user_id), user_card)


# 手动查询命令
stat_command = on_command("今日统计", aliases={"今日发言", "消息统计"}, priority=STAT_COMMAND_PRIORITY, block=True)


@stat_command.handle()
async def handle_stat_command(event: GroupMessageEvent):
    """处理手动查询统计命令"""
    group_id = event.group_id

    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id), "0"):
        await stat_command.finish()
        return

    total = get_total_messages(group_id)
    top_users = get_top_users(group_id)

    if total == 0:
        await stat_command.finish("今日暂无消息统计")

    img_bytes = await render_today_stat_image(total, top_users)
    await stat_command.finish(MessageSegment.image(img_bytes))
