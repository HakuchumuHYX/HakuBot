import time
from typing import Dict, Any
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent
from nonebot.permission import SUPERUSER
from utils.logging import get_logger
from utils.onebot.forward import send_forward_msg
from core.registry import get_plugin_catalog
from core.access_state import (
    cd_config,
    save_cd_config,
    cd_runtime,
    save_cd_runtime,
)

from core.cooldown import get_plugin_cd_duration, check_cd, update_cd, set_plugin_cd

logger = get_logger("plugin_manager")

enable_cd = on_command("启用CD", permission=SUPERUSER, priority=1, block=True)

disable_cd = on_command("禁用CD", permission=SUPERUSER, priority=1, block=True)

enable_feature_cd = on_command(
    "启用功能CD", permission=SUPERUSER, priority=1, block=True
)

disable_feature_cd = on_command(
    "禁用功能CD", permission=SUPERUSER, priority=1, block=True
)

list_cd = on_command("CD列表", permission=SUPERUSER, priority=1, block=True)


@enable_cd.handle()
async def handle_enable_cd(bot: Bot, event: MessageEvent):
    """启用插件CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await enable_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    parts = msg.replace("启用CD", "").strip().split()

    if len(parts) != 2:
        await enable_cd.finish("格式：启用CD <插件名> <CD秒数>\n（仅对当前群生效）")
        return

    plugin_name, cd_seconds_str = parts

    try:
        cd_seconds = int(cd_seconds_str)
        if cd_seconds <= 0:
            await enable_cd.finish("CD秒数必须是大于0的整数")
            return
    except ValueError:
        await enable_cd.finish("CD秒数必须是一个大于0的整数")
        return

    if set_plugin_cd(plugin_name, group_id, cd_seconds):
        await enable_cd.finish(
            f"已在本群启用插件 {plugin_name} 的CD，时长 {cd_seconds} 秒"
        )
    else:
        await enable_cd.finish(f"未登记此插件: {plugin_name}")


@disable_cd.handle()
async def handle_disable_cd(bot: Bot, event: MessageEvent):
    """禁用插件CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await disable_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("禁用CD", "").strip()

    if not plugin_name or " " in plugin_name:
        await disable_cd.finish("格式：禁用CD <插件名>\n（仅对当前群生效）")
        return

    if set_plugin_cd(plugin_name, group_id, 0):  # 设置为0即为禁用
        await disable_cd.finish(f"已在本群禁用插件 {plugin_name} 的CD")
    else:
        await disable_cd.finish(f"未登记此插件: {plugin_name}")


@enable_feature_cd.handle()
async def handle_enable_feature_cd(bot: Bot, event: MessageEvent):
    """启用插件功能CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await enable_feature_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    parts = msg.replace("启用功能CD", "").strip().split()

    if len(parts) != 3:
        await enable_feature_cd.finish(
            "格式：启用功能CD <插件名> <功能名> <CD秒数>\n（仅对当前群生效）"
        )
        return

    plugin_name, feature_name, cd_seconds_str = parts
    plugin_id = f"{plugin_name}:{feature_name}"

    try:
        cd_seconds = int(cd_seconds_str)
        if cd_seconds <= 0:
            await enable_feature_cd.finish("CD秒数必须是大于0的整数")
            return
    except ValueError:
        await enable_feature_cd.finish("CD秒数必须是一个大于0的整数")
        return

    if set_plugin_cd(plugin_id, group_id, cd_seconds):
        await enable_feature_cd.finish(
            f"已在本群启用功能 {plugin_id} 的CD，时长 {cd_seconds} 秒"
        )
    else:
        await enable_feature_cd.finish(f"未登记此功能: {plugin_id}")


@disable_feature_cd.handle()
async def handle_disable_feature_cd(bot: Bot, event: MessageEvent):
    """禁用插件功能CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await disable_feature_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    parts = msg.replace("禁用功能CD", "").strip().split()

    if len(parts) != 2:
        await disable_feature_cd.finish(
            "格式：禁用功能CD <插件名> <功能名>\n（仅对当前群生效）"
        )
        return

    plugin_name, feature_name = parts
    plugin_id = f"{plugin_name}:{feature_name}"

    if set_plugin_cd(plugin_id, group_id, 0):  # 设置为0即为禁用
        await disable_feature_cd.finish(f"已在本群禁用功能 {plugin_id} 的CD")
    else:
        await disable_feature_cd.finish(f"未登记此功能: {plugin_id}")


@list_cd.handle()
async def handle_list_cd(bot: Bot, event: MessageEvent):
    """显示当前群聊的CD配置"""
    if not isinstance(event, GroupMessageEvent):
        await list_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    plugin_catalog = get_plugin_catalog()
    if not plugin_catalog:
        await list_cd.finish("暂无已登记的可管理插件")

    cd_list_msgs = []
    # 遍历注册表中的插件
    for plugin_id, plugin_name in plugin_catalog.items():
        # 获取当前群的CD配置
        cd_duration = get_plugin_cd_duration(plugin_id, group_id)
        status = f"{cd_duration} 秒" if cd_duration > 0 else "无CD"
        cd_list_msgs.append(f"{plugin_name} ({plugin_id}) - {status}")

    if cd_list_msgs:
        message = "当前群聊插件CD配置:\n" + "\n".join(cd_list_msgs)
    else:
        message = "当前群聊暂无插件CD配置"

    try:
        await send_forward_msg(bot, event, items=[message])
    except Exception as e:
        logger.exception(f"合并转发失败: {e}")
        await list_cd.finish(message)
