import time
from typing import Dict, Any
from nonebot import get_driver
from utils.logging import get_logger
from core.registry import get_plugin_catalog
from core.access_state import (
    cd_config,
    save_cd_config,
    cd_runtime,
    save_cd_runtime,
)

logger = get_logger("cooldown")


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


def set_plugin_cd(plugin_id: str, group_id: str, cd_seconds: int) -> bool:
    """内部函数：设置或移除指定群的CD"""
    plugin_catalog = get_plugin_catalog()
    if plugin_id not in plugin_catalog:
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
