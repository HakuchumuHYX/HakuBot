from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Bot, Message
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

# 导入管理模块
from ..plugin_manager import is_plugin_enabled

from .data_manager import data_manager
from .utils import get_total_messages, get_top_users, generate_stat_message
from .config import MESSAGE_HANDLER_PRIORITY, STAT_COMMAND_PRIORITY

# 创建消息处理器
message_handler = on_message(priority=MESSAGE_HANDLER_PRIORITY, block=False)


@message_handler.handle()
async def handle_group_message(event: GroupMessageEvent):
    """处理群消息并更新统计"""
    group_id = event.group_id

    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id)):
        return

    user_id = event.user_id
    user_card = event.sender.card or event.sender.nickname

    # 记录用户消息
    data_manager.record_user_message(group_id, user_id, user_card)


# 手动查询命令
stat_command = on_command("今日统计", aliases={"今日发言", "消息统计"}, priority=STAT_COMMAND_PRIORITY, block=True)


@stat_command.handle()
async def handle_stat_command(event: GroupMessageEvent):
    """处理手动查询统计命令"""
    group_id = event.group_id

    # 检查插件是否启用
    if not is_plugin_enabled("group_statistics", str(group_id)):
        await stat_command.finish("本群未开启消息统计功能")
        return

    total = get_total_messages(group_id)
    top_users = get_top_users(group_id)

    if total == 0:
        await stat_command.finish("今日暂无消息统计")

    message = generate_stat_message(total, top_users, is_daily=False)
    await stat_command.finish(message)