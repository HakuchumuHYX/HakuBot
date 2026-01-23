# plugin_manager/cd_manager.py
import time
from typing import Dict, Any

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent
from nonebot.permission import SUPERUSER

# 从 __init__.py 导入核心数据和 I/O 函数
from . import (
    load_readme_plugins,
    cd_config,  # 导入CD配置字典 (现在是分群的)
    save_cd_config,  # 导入CD配置保存函数
    cd_runtime,  # 导入CD运行时字典
    save_cd_runtime  # 导入CD运行时保存函数
)


def get_plugin_cd_duration(plugin_id: str, group_id: str) -> int:
    """
    获取某个插件/功能在指定群的CD时长（单位：秒）
    :param plugin_id: 插件标识符
    :param group_id: 群号
    :return: CD时长（秒），如果未设置为 0
    """
    # 从分群配置中读取
    return cd_config.get(group_id, {}).get(plugin_id, 0)


def check_cd(plugin_id: str, group_id: str, user_id: str) -> int:
    """
    检查用户是否处于CD中
    :return: 剩余CD时间（秒）。如果为 0，则表示CD结束。
    """
    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return 0  # SuperUser无视CD
    except:
        pass  # 驱动未加载等异常情况，继续执行

    cd_duration = get_plugin_cd_duration(plugin_id, group_id)

    if cd_duration == 0:
        return 0  # 插件未设置CD

    last_call_time = cd_runtime.get(group_id, {}).get(user_id, {}).get(plugin_id, 0)
    now = time.time()

    passed_time = now - last_call_time
    if passed_time >= cd_duration:
        return 0
    else:
        return int(cd_duration - passed_time)


def update_cd(plugin_id: str, group_id: str, user_id: str):
    """
    更新用户的CD时间戳（标记为“已使用”）
    """
    try:
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return  # SuperUser不记录CD
    except:
        pass

    if get_plugin_cd_duration(plugin_id, group_id) > 0:

        now = time.time()
        if group_id not in cd_runtime:
            cd_runtime[group_id] = {}
        if user_id not in cd_runtime[group_id]:
            cd_runtime[group_id][user_id] = {}

        cd_runtime[group_id][user_id][plugin_id] = now
        save_cd_runtime(cd_runtime)


# --- SuperUser 命令 ---

enable_cd = on_command("启用CD", permission=SUPERUSER, priority=1, block=True)
disable_cd = on_command("禁用CD", permission=SUPERUSER, priority=1, block=True)
enable_feature_cd = on_command("启用功能CD", permission=SUPERUSER, priority=1, block=True)
disable_feature_cd = on_command("禁用功能CD", permission=SUPERUSER, priority=1, block=True)
list_cd = on_command("CD列表", permission=SUPERUSER, priority=1, block=True)


def set_plugin_cd(plugin_id: str, group_id: str, cd_seconds: int) -> bool:
    """内部函数：设置或移除指定群的CD"""
    readme_plugins = load_readme_plugins()
    if plugin_id not in readme_plugins:
        return False  # 插件不存在

    if cd_seconds > 0:
        # 设置CD
        if group_id not in cd_config:
            cd_config[group_id] = {}
        cd_config[group_id][plugin_id] = cd_seconds
    else:
        # 移除CD
        if group_id in cd_config and plugin_id in cd_config[group_id]:
            del cd_config[group_id][plugin_id]
            # 如果该群配置为空，则移除该群
            if not cd_config[group_id]:
                del cd_config[group_id]

    save_cd_config(cd_config)  # 使用导入的保存函数
    return True


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
        await enable_cd.finish(f"已在本群启用插件 {plugin_name} 的CD，时长 {cd_seconds} 秒")
    else:
        await enable_cd.finish(f"未在 readme.md 中找到插件: {plugin_name}")


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
        await disable_cd.finish(f"未在 readme.md 中找到插件: {plugin_name}")


@enable_feature_cd.handle()
async def handle_enable_feature_cd(bot: Bot, event: MessageEvent):
    """启用插件功能CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await enable_feature_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    parts = msg.replace("启用功能CD", "").strip().split()

    if len(parts) != 3:
        await enable_feature_cd.finish("格式：启用功能CD <插件名> <功能名> <CD秒数>\n（仅对当前群生效）")
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
        await enable_feature_cd.finish(f"已在本群启用功能 {plugin_id} 的CD，时长 {cd_seconds} 秒")
    else:
        await enable_feature_cd.finish(f"未在 readme.md 中找到功能: {plugin_id}")


@disable_feature_cd.handle()
async def handle_disable_feature_cd(bot: Bot, event: MessageEvent):
    """禁用插件功能CD（仅限当前群）"""
    if not isinstance(event, GroupMessageEvent):
        await disable_feature_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    msg = event.get_plaintext().strip()
    parts = msg.replace("禁用功能CD", "").strip().split()

    if len(parts) != 2:
        await disable_feature_cd.finish("格式：禁用功能CD <插件名> <功能名>\n（仅对当前群生效）")
        return

    plugin_name, feature_name = parts
    plugin_id = f"{plugin_name}:{feature_name}"

    if set_plugin_cd(plugin_id, group_id, 0):  # 设置为0即为禁用
        await disable_feature_cd.finish(f"已在本群禁用功能 {plugin_id} 的CD")
    else:
        await disable_feature_cd.finish(f"未在 readme.md 中找到功能: {plugin_id}")


@list_cd.handle()
async def handle_list_cd(bot: Bot, event: MessageEvent):
    """显示当前群聊的CD配置"""
    if not isinstance(event, GroupMessageEvent):
        await list_cd.finish("请在群聊中使用此命令")

    group_id = str(event.group_id)
    readme_plugins = load_readme_plugins()
    if not readme_plugins:
        await list_cd.finish("未找到插件列表配置，请检查 readme.md 文件")

    cd_list_msgs = []
    # 遍历 readme 中的所有插件
    for plugin_id, plugin_name in readme_plugins.items():
        # 获取当前群的CD配置
        cd_duration = get_plugin_cd_duration(plugin_id, group_id)
        status = f"{cd_duration} 秒" if cd_duration > 0 else "无CD"
        cd_list_msgs.append(f"{plugin_name} ({plugin_id}) - {status}")

    if cd_list_msgs:
        message = "当前群聊插件CD配置:\n" + "\n".join(cd_list_msgs)
    else:
        message = "当前群聊暂无插件CD配置"

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
        await list_cd.finish(message)
