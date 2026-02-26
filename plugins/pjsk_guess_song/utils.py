# pjsk_guess_song/utils.py
"""
存放所有辅助函数和检查逻辑
"""
import time
from datetime import datetime
from typing import Tuple, Optional
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot import get_bot
from nonebot.log import logger

# 导入服务和配置
from . import db_service, plugin_config
# 导入全局状态
from .game_data import last_game_end_time, active_game_sessions


def get_session_id(event: MessageEvent) -> str:
    """为 nonebot event 生成一个唯一的会话 ID"""
    if isinstance(event, GroupMessageEvent):
        return f"onebot:group:{event.group_id}"
    else:
        # 私聊
        return f"onebot:private:{event.user_id}"


def get_user_id(event: MessageEvent) -> str:
    return str(event.user_id)


def get_user_name(event: MessageEvent) -> str:
    return event.sender.card or event.sender.nickname or str(event.user_id)


async def _is_group_allowed(event: MessageEvent) -> bool:
    """检查群组是否在白名单中"""
    whitelist = plugin_config.group_whitelist
    if not whitelist:
        return True  # 白名单为空，允许所有

    if isinstance(event, GroupMessageEvent):
        is_in_whitelist = str(event.group_id) in whitelist
        if not is_in_whitelist:
            try:
                # 尝试发送消息，失败也无妨
                await get_bot().send(event, "本群未启用猜歌功能")
            except Exception as e:
                logger.error(f"发送非白名单群聊消息失败: {e}")
        return is_in_whitelist

    return True  # 私聊默认允许


def _get_setting_for_group(event: MessageEvent, key: str, default: any) -> any:
    """
    Nonebot 适配版设置获取。
    直接从加载的 plugin_config 对象中读取属性。
    """
    return getattr(plugin_config, key, default)


async def _check_game_start_conditions(event: MessageEvent) -> Tuple[bool, Optional[str]]:
    """检查是否可以开始新游戏"""
    if not await _is_group_allowed(event):
        return False, None

    # --- 检查游戏是否在禁用时段 ---
    now_time = datetime.now().time()
    disable_periods = _get_setting_for_group(event, "disable_guess_song_periods", [])
    if isinstance(disable_periods, list):
        for period in disable_periods:
            try:
                start_time = datetime.strptime(period["start"], "%H:%M").time()
                end_time = datetime.strptime(period["end"], "%H:%M").time()
                if start_time <= now_time < end_time:
                    default_msg = f"当前时段 ({period['start']} - {period['end']}) 猜歌功能已禁用。"
                    return False, period.get("message", default_msg)
            except (KeyError, ValueError) as e:
                logger.warning(f"跳过格式错误的禁用时段配置: {period}, 错误: {e}")
                continue

    session_id = get_session_id(event)
    cooldown = _get_setting_for_group(event, "game_cooldown_seconds", 30)
    limit = _get_setting_for_group(event, "daily_play_limit", 15)
    debug_mode = plugin_config.debug_mode
    is_independent_limit = _get_setting_for_group(event, "independent_daily_limit", False)

    if not debug_mode and time.time() - last_game_end_time.get(session_id, 0) < cooldown:
        remaining_time = cooldown - (time.time() - last_game_end_time.get(session_id, 0))
        time_display = f"{remaining_time:.3f}" if remaining_time < 1 else str(int(remaining_time))
        return False, f"嗯......休息 {time_display} 秒再玩吧......"

    if session_id in active_game_sessions:
        return False, "......有一个正在进行的游戏了呢。"

    can_play = await db_service.can_play(get_user_id(event), limit, session_id, is_independent_limit)
    if not debug_mode and not can_play:
        limit_type = "本群" if is_independent_limit else "你"
        return False, f"......{limit_type}今天的游戏次数已达上限（{limit}次），请明天再来吧......"

    return True, None