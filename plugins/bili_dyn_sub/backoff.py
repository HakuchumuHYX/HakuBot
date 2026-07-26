"""bili_dyn_sub per-UID 退避状态机（设计文档 §6 与 §3.5）。

退避语义借鉴 nonebot-bison (MIT, Copyright (c) 2021 felinae98) 的
platform/bilibili/retry.py：352 风控先强制刷新 cookie 重试，仍失败则 5min × 2ⁿ 指数退避。
但 bison 用 490 行泛型 FSM（retry.py + fsm.py）实现，且自己在 retry.py 里留了
`# FIXME: 全局单例会导致所有被装饰的函数共享状态` —— 一个 UID 吃风控会把所有订阅拖进退避。
本模块用 dict[uid, BackoffState] 做**per-UID 隔离**的计数器替代之。

三类错误的退避强度不同：
- 352 风控：前 MAX_REFRESH_COUNT 次返回 "refresh_cookie"（换 cookie 重试），之后 5min × 2ⁿ（上限 1h）
- HTTP 412：IP 层风控，换 cookie 无效，30min × 2ⁿ（上限 4h），last_error 标记为 "ip_block"
- 网络错误：不是风控，短退避 60s，且不消耗 refresh_count

本模块是纯逻辑：无 IO、无网络、不读配置，时间统一走 time.time()，
所有涉及时间的方法都带可选 now 参数便于单测注入。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Literal, Optional

from ..utils.tools import get_logger

logger = get_logger("bili_dyn_sub.backoff")

# 352 风控：前 2 次只强制刷新 cookie 重试，之后才进入指数退避
MAX_REFRESH_COUNT = 2
RISK_BACKOFF_BASE_SECONDS = 300.0  # 5min
RISK_BACKOFF_MAX_SECONDS = 3600.0  # 1h
# HTTP 412 是 IP 层风控（机房 IP 高发），退避远长于 352
IP_BLOCK_BACKOFF_BASE_SECONDS = 1800.0  # 30min
IP_BLOCK_BACKOFF_MAX_SECONDS = 14400.0  # 4h
# 网络错误（超时/连接失败）只做短退避
NETWORK_BACKOFF_SECONDS = 60.0

# on_risk_control 的返回值字面量
ACTION_REFRESH_COOKIE = "refresh_cookie"
ACTION_BACKOFF = "backoff"
RiskAction = Literal["refresh_cookie", "backoff"]

# last_error 的固定标记：IP 层风控，调用方可据此提示配置 proxy
ERROR_IP_BLOCK = "ip_block"
# 网络错误的 last_error 前缀
ERROR_NETWORK_PREFIX = "network:"


@dataclass
class BackoffState:
    """单个 UID 的退避状态"""

    fail_count: int = 0  # 连续风控类失败次数（352 / 412），成功即清零
    refresh_count: int = 0  # 已因 352 强制刷新 cookie 的次数，上限 MAX_REFRESH_COUNT
    backoff_until: Optional[float] = None  # 退避截止时间戳；None 表示当前不在退避
    last_error: str = ""  # 最近一次错误标记；等于 ERROR_IP_BLOCK 时代表 IP 被风控
    last_log_key: str = ""  # 最近一次已打过 warning 的 error_key，用于日志纪律（§3.5 末）


class BackoffManager:
    """per-UID 退避状态机。单事件循环内使用，不加锁。"""

    def __init__(self) -> None:
        self._states: dict[str, BackoffState] = {}

    # ---------- 内部工具 ----------

    @staticmethod
    def _key(uid: object) -> str:
        """uid 统一成字符串 key（调用方可能传 int）"""
        return str(uid).strip()

    def _state(self, uid: object) -> BackoffState:
        """取（必要时创建）某 uid 的状态"""
        key = self._key(uid)
        state = self._states.get(key)
        if state is None:
            state = BackoffState()
            self._states[key] = state
        return state

    def _peek(self, uid: object) -> Optional[BackoffState]:
        """只读查询，不为未知 uid 创建状态"""
        return self._states.get(self._key(uid))

    def _enter_backoff(self, state: BackoffState, delay: float, now: float) -> float:
        """进入退避；已有更晚的截止时间则保留（短退避不能缩短长退避）"""
        state.backoff_until = max(state.backoff_until or 0.0, now + delay)
        return state.backoff_until

    # ---------- 查询 ----------

    def is_backing_off(self, uid: object, now: Optional[float] = None) -> bool:
        """该 uid 是否仍在退避窗口内；窗口自然到期时顺手清掉截止时间"""
        state = self._peek(uid)
        if state is None or state.backoff_until is None:
            return False
        current = time.time() if now is None else now
        if current < state.backoff_until:
            return True
        state.backoff_until = None  # 退避到期，但保留失败计数用于后续升级
        return False

    def remaining_seconds(self, uid: object, now: Optional[float] = None) -> int:
        """剩余退避秒数（向上取整）；不在退避中返回 0"""
        state = self._peek(uid)
        if state is None or state.backoff_until is None:
            return 0
        current = time.time() if now is None else now
        return max(0, math.ceil(state.backoff_until - current))

    def get_state(self, uid: object) -> BackoffState:
        """返回状态快照（副本，外部无法改到内部状态）"""
        state = self._peek(uid)
        return replace(state) if state is not None else BackoffState()

    def is_ip_blocked(self, uid: object) -> bool:
        """最近一次失败是否为 IP 层风控（调用方据此提示配置 proxy）"""
        state = self._peek(uid)
        return state is not None and state.last_error == ERROR_IP_BLOCK

    # ---------- 状态迁移 ----------

    def on_success(self, uid: object) -> None:
        """本轮成功：重置该 uid 的全部状态（含日志纪律标记，恢复后再出错要重新告警）"""
        key = self._key(uid)
        state = self._states.get(key)
        if state is None:
            return
        if state.fail_count or state.refresh_count or state.backoff_until or state.last_error:
            logger.info(f"UID {key} 取数恢复正常（此前失败 {state.fail_count} 次，最近错误: {state.last_error or '-'}）")
        self._states[key] = BackoffState()

    def on_risk_control(self, uid: object, reason: str, now: Optional[float] = None) -> RiskAction:
        """
        352 风控：前 MAX_REFRESH_COUNT 次返回 "refresh_cookie"（调用方应强制刷新 cookie 后重试），
        达到刷新上限后进入 5min × 2ⁿ 指数退避（上限 1h）并返回 "backoff"
        """
        current = time.time() if now is None else now
        state = self._state(uid)
        state.fail_count += 1
        state.last_error = reason
        if state.refresh_count < MAX_REFRESH_COUNT:
            state.refresh_count += 1
            logger.debug(
                f"UID {self._key(uid)} 触发风控（{reason}），"
                f"强制刷新 cookie 重试 {state.refresh_count}/{MAX_REFRESH_COUNT}"
            )
            return ACTION_REFRESH_COOKIE
        # 刷新窗口之后的第 n 次失败对应 2ⁿ 倍退避（n 从 0 起）
        step = max(0, state.fail_count - MAX_REFRESH_COUNT - 1)
        delay = min(RISK_BACKOFF_BASE_SECONDS * (2**step), RISK_BACKOFF_MAX_SECONDS)
        self._enter_backoff(state, delay, current)
        logger.debug(f"UID {self._key(uid)} 刷新 cookie 仍风控（{reason}），退避 {int(delay)}s")
        return ACTION_BACKOFF

    def on_ip_block(self, uid: object, reason: str, now: Optional[float] = None) -> None:
        """HTTP 412：IP 层风控，换 cookie 无效，30min × 2ⁿ 退避（上限 4h）并标记 last_error"""
        current = time.time() if now is None else now
        state = self._state(uid)
        state.fail_count += 1
        state.last_error = ERROR_IP_BLOCK
        step = max(0, state.fail_count - 1)
        delay = min(IP_BLOCK_BACKOFF_BASE_SECONDS * (2**step), IP_BLOCK_BACKOFF_MAX_SECONDS)
        self._enter_backoff(state, delay, current)
        logger.debug(f"UID {self._key(uid)} 命中 IP 层风控（{reason}），退避 {int(delay)}s")

    def on_network_error(self, uid: object, reason: str, now: Optional[float] = None) -> None:
        """网络错误：短退避，且不计入 refresh_count / fail_count（不是风控，不该升级）"""
        current = time.time() if now is None else now
        state = self._state(uid)
        state.last_error = f"{ERROR_NETWORK_PREFIX}{reason}"
        self._enter_backoff(state, NETWORK_BACKOFF_SECONDS, current)
        logger.debug(f"UID {self._key(uid)} 网络错误（{reason}），退避 {int(NETWORK_BACKOFF_SECONDS)}s")

    # ---------- 日志纪律 ----------

    def should_log_warning(self, uid: object, error_key: str) -> bool:
        """
        同一 error_key 连续出现只在状态跃迁（error_key 变化）时返回 True，其余返回 False，
        避免风控刷屏（设计文档 §3.5 末）。调用方在返回 False 时应降级为 debug 日志。
        """
        state = self._state(uid)
        key = (error_key or "").strip()
        if key == state.last_log_key:
            return False
        state.last_log_key = key
        return True

    # ---------- 清理 ----------

    def forget(self, uid: object) -> None:
        """退订后丢弃该 uid 的退避状态，避免 dict 无界增长"""
        self._states.pop(self._key(uid), None)

    def reset_all(self) -> None:
        """清空全部状态（仅测试 / 重载配置时使用）"""
        self._states.clear()


# 模块级单例：状态按 uid 隔离，进程内共享一份
backoff_manager = BackoffManager()
