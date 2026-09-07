from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional
from nonebot import get_bot, get_driver, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.exception import ActionFailed, NetworkError

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler as apscheduler
from utils.logging import get_exc_desc, get_logger
from utils.concurrency import run_in_pool
from utils.onebot.forward import send_forward_msg
from plugins.bili_dyn_sub import api
from plugins.bili_dyn_sub.backoff import ACTION_REFRESH_COOKIE, backoff_manager
from plugins.bili_dyn_sub.config import plugin_config
from plugins.bili_dyn_sub.credential import LoginStatus, credential_manager
from plugins.bili_dyn_sub.parser import ParsedDynamic, parse_feed, should_skip
from plugins.bili_dyn_sub.render import build_messages
from plugins.bili_dyn_sub.store import dyn_id_to_int, store


logger = get_logger("bili_dyn_sub.scheduler")

POLL_JOB_ID = "bili_dyn_sub_poll"

PRUNE_JOB_ID = "bili_dyn_sub_prune"

LOGIN_CHECK_JOB_ID = "bili_dyn_sub_login_check"

LOGIN_CHECK_INTERVAL_HOURS = 6

LOGIN_CHECK_STARTUP_DELAY_SECONDS = 30.0

_EMPTY_FEED_CURSOR = 1

_EMPTY_FEED_BASELINE_THRESHOLD = 3

_EMPTY_FEED_WARN_THRESHOLD = 5

_empty_feed_streak: dict[str, int] = {}

_MIN_SEND_ATTEMPTS = 1


def _cfg_float(name: str, default: float, minimum: float = 0.0) -> float:
    """读取 float 配置项并做下限保护（config.py 未加数值 validator）"""
    try:
        value = float(getattr(plugin_config, name, default))
    except (TypeError, ValueError):
        logger.warning(f"配置项 {name} 非法，回落默认值 {default}")
        return default
    return max(minimum, value)


def _cfg_int(name: str, default: int, minimum: int = 0) -> int:
    """读取 int 配置项并做下限保护"""
    try:
        value = int(getattr(plugin_config, name, default))
    except (TypeError, ValueError):
        logger.warning(f"配置项 {name} 非法，回落默认值 {default}")
        return default
    return max(minimum, value)


async def _save_state() -> None:
    """把去重状态落盘（同步原子写下线程池，不阻塞事件循环）"""
    await run_in_pool(store.save)


def _log_error(uid: str, error_key: str, message: str) -> None:
    """日志纪律（§3.5）：同一错误码只在状态跃迁时 warn，连续复发降级为 debug"""
    if backoff_manager.should_log_warning(uid, error_key):
        logger.warning(message)
    else:
        logger.debug(message)


_last_known_login: Optional[bool] = None

FORCED_LOGIN_CHECK_COOLDOWN_SECONDS = 600.0

_last_forced_login_check_ts: float = 0.0

_startup_login_task: Optional[asyncio.Task] = None
