# plugin_manager/__init__.py
import json
import os
from pathlib import Path
from typing import Dict, Set

from nonebot import on_command, get_loaded_plugins
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import Plugin

# 存储文件路径
DATA_FILE = Path("data/plugin_manager/plugin_status.json")
README_FILE = Path(__file__).parent / "readme.md"  # 添加 readme.md 文件路径

# 确保数据目录存在
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_readme_plugins() -> Dict[str, str]:
    """从 readme.md 加载插件列表"""
    plugins = {}

    if not README_FILE.exists():
        return plugins

    try:
        with open(README_FILE, 'r', encoding='utf-8') as f:
            content = f.read()

        # 解析 readme.md 内容，提取插件名称和标识符
        lines = content.split('\n')
        for line in lines:
            if '：' in line or ':' in line:
                # 支持中文冒号和英文冒号
                separator = '：' if '：' in line else ':'
                if separator in line:
                    parts = line.split(separator, 1)
                    if len(parts) == 2:
                        plugin_name = parts[0].strip()
                        plugin_id = parts[1].strip()
                        plugins[plugin_id] = plugin_name
    except Exception as e:
        print(f"读取 readme.md 失败: {e}")

    return plugins


# 加载插件状态数据
def load_plugin_status() -> Dict[str, Dict[str, bool]]:
    """加载插件状态数据"""
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_plugin_status(data: Dict[str, Dict[str, bool]]):
    """保存插件状态数据"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 全局插件状态存储
plugin_status = load_plugin_status()


def is_plugin_enabled(plugin_name: str, group_id: str) -> bool:
    """检查插件在指定群是否启用"""
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
    save_plugin_status(plugin_status)


# 启用插件命令 - 仅SU可用
enable_plugin = on_command("启用", permission=SUPERUSER, priority=1, block=True)
disable_plugin = on_command("禁用", permission=SUPERUSER, priority=1, block=True)
list_plugins = on_command("插件列表", permission=SUPERUSER, priority=1, block=True)
enable_all = on_command("启用all", permission=SUPERUSER, priority=1, block=True)
disable_all = on_command("禁用all", permission=SUPERUSER, priority=1, block=True)


@enable_plugin.handle()
async def handle_enable(bot: Bot, event: MessageEvent):
    """启用插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_plugin.finish("请在群聊中使用此命令")

    msg = event.get_plaintext().strip()
    plugin_name = msg.replace("启用", "").strip()

    if not plugin_name:
        await enable_plugin.finish("请指定要启用的插件名称，例如：启用help")

    # 获取所有已加载的插件
    plugins = get_loaded_plugins()
    plugin_names = {p.name for p in plugins}

    if plugin_name not in plugin_names:
        await enable_plugin.finish(f"未找到插件: {plugin_name}")

    set_plugin_status(plugin_name, str(event.group_id), True)
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

    # 获取所有已加载的插件
    plugins = get_loaded_plugins()
    plugin_names = {p.name for p in plugins}

    if plugin_name not in plugin_names:
        await disable_plugin.finish(f"未找到插件: {plugin_name}")

    set_plugin_status(plugin_name, str(event.group_id), False)
    await disable_plugin.finish(f"已禁用插件: {plugin_name}")


@list_plugins.handle()
async def handle_list(bot: Bot, event: MessageEvent):
    """显示插件列表"""
    if not isinstance(event, GroupMessageEvent):
        await list_plugins.finish("请在群聊中使用此命令")

    # 实时加载 readme.md 中的插件列表
    readme_plugins = load_readme_plugins()
    group_id = str(event.group_id)

    if not readme_plugins:
        await list_plugins.finish("未找到插件列表配置，请检查 readme.md 文件")

    plugin_list = []
    for plugin_id, plugin_name in readme_plugins.items():
        status = "✅ 启用" if is_plugin_enabled(plugin_id, group_id) else "❌ 禁用"
        plugin_list.append(f"{plugin_name} ({plugin_id}) - {status}")

    if plugin_list:
        message = "目前接入了管理插件的有:\n" + "\n".join(plugin_list)
    else:
        message = "暂无其他插件"

    await list_plugins.finish(message)


@enable_all.handle()
async def handle_enable_all(bot: Bot, event: MessageEvent):
    """启用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await enable_all.finish("请在群聊中使用此命令")

    # 从 readme.md 加载插件列表
    readme_plugins = load_readme_plugins()
    group_id = str(event.group_id)

    if not readme_plugins:
        await enable_all.finish("未找到插件列表配置，请检查 readme.md 文件")

    # 获取所有已加载的插件
    loaded_plugins = get_loaded_plugins()
    loaded_plugin_names = {p.name for p in loaded_plugins}

    # 启用所有在 readme.md 中列出且已加载的插件
    enabled_count = 0
    for plugin_id in readme_plugins.keys():
        if plugin_id in loaded_plugin_names:
            set_plugin_status(plugin_id, group_id, True)
            enabled_count += 1

    await enable_all.finish(f"已启用 {enabled_count} 个插件")


@disable_all.handle()
async def handle_disable_all(bot: Bot, event: MessageEvent):
    """禁用所有插件"""
    if not isinstance(event, GroupMessageEvent):
        await disable_all.finish("请在群聊中使用此命令")

    # 从 readme.md 加载插件列表
    readme_plugins = load_readme_plugins()
    group_id = str(event.group_id)

    if not readme_plugins:
        await disable_all.finish("未找到插件列表配置，请检查 readme.md 文件")

    # 获取所有已加载的插件
    loaded_plugins = get_loaded_plugins()
    loaded_plugin_names = {p.name for p in loaded_plugins}

    # 禁用所有在 readme.md 中列出且已加载的插件
    disabled_count = 0
    for plugin_id in readme_plugins.keys():
        if plugin_id in loaded_plugin_names:
            set_plugin_status(plugin_id, group_id, False)
            disabled_count += 1

    await disable_all.finish(f"已禁用 {disabled_count} 个插件")
