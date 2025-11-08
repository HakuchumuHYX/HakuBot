# plugin_manager/enable.py
from nonebot import on_command, get_loaded_plugins, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import Plugin
from typing import Dict

# 从 __init__.py 导入核心数据和 I/O 函数
from . import (
    load_readme_plugins,
    plugin_status,  # 导入全局状态字典
    save_plugin_status  # 导入保存函数
)


# --- 插件/功能开关的核心API函数 ---

def is_plugin_enabled(plugin_name: str, group_id: str, user_id: str) -> bool:
    """检查插件在指定群是否启用"""

    # 【修改：添加SU检查】
    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True  # SuperUser无视开关
    except:
        pass
    # ^^^^^^ 【修改：添加SU检查】 ^^^^^^

    # 默认启用
    if plugin_name not in plugin_status:
        return True
    if group_id not in plugin_status[plugin_name]:
        return True
    return plugin_status[plugin_name][group_id]


def set_plugin_status(plugin_name: str, group_id: str, enabled: bool):
    """设置插件状态"""
    if plugin_name not in plugin_status:
        plugin_status[plugin_name] = {}
    plugin_status[plugin_name][group_id] = enabled
    save_plugin_status(plugin_status)  # 使用导入的保存函数


def is_feature_enabled(plugin_name: str, feature_name: str, group_id: str, user_id: str) -> bool:
    """检查插件的特定功能是否启用"""

    # 【修改：添加SU检查】
    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True  # SuperUser无视开关
    except:
        pass
    # ^^^^^^ 【修改：添加SU检查】 ^^^^^^

    # 先检查整个插件是否启用 (传递 user_id)
    if not is_plugin_enabled(plugin_name, group_id, user_id):
        return False

    # 检查特定功能状态
    feature_key = f"{plugin_name}:{feature_name}"
    if feature_key not in plugin_status:
        return True  # 默认启用
    if group_id not in plugin_status[feature_key]:
        return True  # 默认启用
    return plugin_status[feature_key][group_id]


def set_feature_status(plugin_name: str, feature_name: str, group_id: str, enabled: bool):
    """设置插件特定功能状态"""
    feature_key = f"{plugin_name}:{feature_name}"
    if feature_key not in plugin_status:
        plugin_status[feature_key] = {}
    plugin_status[feature_key][group_id] = enabled
    save_plugin_status(plugin_status)  # 使用导入的保存函数

# --- SuperUser 命令处理 ---

enable_plugin = on_command("启用", permission=SUPERUSER, priority=1, block=True)
disable_plugin = on_command("禁用", permission=SUPERUSER, priority=1, block=True)
list_plugins = on_command("插件开关状态", permission=SUPERUSER, priority=1, block=True)
enable_all = on_command("启用all", permission=SUPERUSER, priority=1, block=True)
disable_all = on_command("禁用all", permission=SUPERUSER, priority=1, block=True)
enable_feature = on_command("启用功能", permission=SUPERUSER, priority=1, block=True)
disable_feature = on_command("禁用功能", permission=SUPERUSER, priority=1, block=True)


@enable_plugin.handle()
async def handle_enable(bot: Bot, event: MessageEvent):
    """启用插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_plugin.finish("请在群聊中使用此命令")

    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("启用", "").strip()

    if not plugin_name:
        await enable_plugin.finish("请指定要启用的插件名称，例如：启用help")

    # 检查插件
    readme_plugins = load_readme_plugins()
    if plugin_name not in readme_plugins:
        plugins = get_loaded_plugins()
        plugin_names = {p.name for p in plugins}
        if plugin_name not in plugin_names:
            await enable_plugin.finish(f"未找到插件: {plugin_name}")
            return

    set_plugin_status(plugin_name, str(event.group_id), True)  # 调用此文件中的 set_plugin_status
    await enable_plugin.finish(f"已启用插件: {plugin_name}")


@disable_plugin.handle()
async def handle_disable(bot: Bot, event: MessageEvent):
    """禁用插件"""
    if not isinstance(event, GroupMessageEvent):
        await disable_plugin.finish("请在群聊中使用此命令")

    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("禁用", "").strip()

    if not plugin_name:
        await disable_plugin.finish("请指定要禁用的插件名称，例如：禁用help")

    # 检查插件
    readme_plugins = load_readme_plugins()
    if plugin_name not in readme_plugins:
        plugins = get_loaded_plugins()
        plugin_names = {p.name for p in plugins}
        if plugin_name not in plugin_names:
            await disable_plugin.finish(f"未找到插件: {plugin_name}")
            return

    set_plugin_status(plugin_name, str(event.group_id), False)  # 调用此文件中的 set_plugin_status
    await disable_plugin.finish(f"已禁用插件: {plugin_name}")


@list_plugins.handle()
async def handle_list(bot: Bot, event: MessageEvent):
    """显示插件列表 - 以合并转发形式"""
    if not isinstance(event, GroupMessageEvent):
        await list_plugins.finish("请在群聊中使用此命令")

    readme_plugins = load_readme_plugins()  # 导入的
    group_id = str(event.group_id)

    if not readme_plugins:
        await list_plugins.finish("未找到插件列表配置，请检查 readme.md 文件")

    plugin_list = []
    for plugin_id, plugin_name in readme_plugins.items():
        status = "✅ 启用" if is_plugin_enabled(plugin_id, group_id, "0") else "❌ 禁用"  # 本地的
        plugin_list.append(f"{plugin_name} ({plugin_id}) - {status}")

    if plugin_list:
        message = "目前接入了管理插件的有:\n" + "\n".join(plugin_list)
    else:
        message = "暂无其他插件"

    try:
        bot_info = await bot.get_login_info()
        bot_uin = bot_info['user_id']
        bot_nickname = bot_info['nickname']

        forward_nodes = [
            {
                "type": "node",
                "data": {
                    "name": bot_nickname,
                    "uin": str(bot_uin),
                    "content": message
                }
            }
        ]
        await bot.send_forward_msg(
            group_id=event.group_id,
            messages=forward_nodes
        )
    except Exception as e:
        print(f"合并转发失败: {e}")
        await list_plugins.finish(message)


@enable_all.handle()
async def handle_enable_all(bot: Bot, event: MessageEvent):
    """启用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_all.finish("请在群聊中使用此命令")

    readme_plugins = load_readme_plugins()
    group_id = str(event.group_id)

    if not readme_plugins:
        await enable_all.finish("未找到插件列表配置，请检查 readme.md 文件")

    enabled_count = 0
    for plugin_id in readme_plugins.keys():
        set_plugin_status(plugin_id, group_id, True)
        enabled_count += 1

    await enable_all.finish(f"已启用 {enabled_count} 个插件")


@disable_all.handle()
async def handle_disable_all(bot: Bot, event: MessageEvent):
    """禁用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await disable_all.finish("请在群聊中使用此命令")

    readme_plugins = load_readme_plugins()
    group_id = str(event.group_id)

    if not readme_plugins:
        await disable_all.finish("未找到插件列表配置，请检查 readme.md 文件")

    disabled_count = 0
    for plugin_id in readme_plugins.keys():
        set_plugin_status(plugin_id, group_id, False)
        disabled_count += 1

    await disable_all.finish(f"已禁用 {disabled_count} 个插件")


@enable_feature.handle()
async def handle_enable_feature(bot: Bot, event: MessageEvent):
    """启用插件特定功能"""
    if not isinstance(event, GroupMessageEvent):
        await enable_feature.finish("请在群聊中使用此命令")
        return

    msg = event.get_plaintext().strip()
    parts = msg.replace("启用功能", "").strip().split()

    if len(parts) != 2:
        await enable_feature.finish("格式：启用功能 插件名 功能名")
        return

    plugin_name, feature_name = parts

    set_feature_status(plugin_name, feature_name, str(event.group_id), True)  # 本地的
    await enable_feature.finish(f"已启用插件 {plugin_name} 的 {feature_name} 功能")


@disable_feature.handle()
async def handle_disable_feature(bot: Bot, event: MessageEvent):
    """禁用插件特定功能"""
    if not isinstance(event, GroupMessageEvent):
        await disable_feature.finish("请在群聊中使用此命令")
        return

    msg = event.get_plaintext().strip()
    parts = msg.replace("禁用功能", "").strip().split()

    if len(parts) != 2:
        await disable_feature.finish("格式：禁用功能 插件名 功能名")
        return

    plugin_name, feature_name = parts

    set_feature_status(plugin_name, feature_name, str(event.group_id), False)  # 本地的
    await disable_feature.finish(f"已禁用插件 {plugin_name} 的 {feature_name} 功能")