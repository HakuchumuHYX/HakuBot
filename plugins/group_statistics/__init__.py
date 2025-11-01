from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata
from nonebot.message import event_preprocessor
from nonebot.adapters.onebot.v11 import Event

require("nonebot_plugin_apscheduler")

# 导入管理模块
from ..plugin_manager import is_plugin_enabled

from .data_manager import data_manager
from .handlers import message_handler, stat_command
from .scheduler import daily_statistics_task

__plugin_meta__ = PluginMetadata(
    name="群聊消息统计",
    description="统计群聊消息数量并生成每日排行榜",
    usage="自动统计，每日0点发送统计结果",
    type="application",
    supported_adapters={"~onebot.v11"},
)

# Bot.send方法补丁
original_send = None


def patch_bot_send():
    """修补Bot的send方法以捕获所有发送的消息"""
    global original_send

    from nonebot.adapters.onebot.v11 import Bot as V11Bot

    if original_send is None:
        original_send = V11Bot.send

    async def patched_send(self, event, message, **kwargs):
        # 调用原始send方法
        result = await original_send(self, event, message, **kwargs)

        # 检查是否是群消息
        if hasattr(event, 'group_id'):
            group_id = event.group_id
            # 检查插件是否启用
            if is_plugin_enabled("group_statistics", str(group_id)):
                # 记录机器人消息
                data_manager.set_bot_self_id(str(self.self_id))
                data_manager.record_bot_message(group_id, "ATRI")

        return result

    # 应用补丁
    V11Bot.send = patched_send


# 事件预处理器捕获机器人消息
@event_preprocessor
async def intercept_bot_messages(event: Event):
    """拦截机器人发送的消息"""
    # 检查是否是机器人发送的消息
    if hasattr(event, '_bot') and hasattr(event, 'message') and hasattr(event, 'group_id'):
        # 检查插件是否启用
        if is_plugin_enabled("group_statistics", str(event.group_id)):
            # 这是一个机器人发送的消息
            data_manager.set_bot_self_id(str(event._bot.self_id))
            data_manager.record_bot_message(event.group_id, "ATRI")


# 在插件加载时应用补丁
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    try:
        patch_bot_send()
        print("已应用Bot.send补丁")
    except Exception as e:
        print(f"应用Bot.send补丁失败: {e}")

    print("群聊消息统计插件已加载")


# 机器人关闭时保存数据
@get_driver().on_shutdown
async def shutdown_plugin():
    """插件关闭时保存数据"""
    data_manager.save_stats()
    print("群聊消息统计插件数据已保存")