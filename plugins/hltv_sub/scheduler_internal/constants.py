"""
scheduler 常量集中定义
"""

from __future__ import annotations

from typing import Literal

JOB_ID = "hltv_check"
DEFAULT_INTERVAL_MINUTES = 3

# start_date 前多少小时进入 UPCOMING（仅在这个窗口内才恢复轮询）
UPCOMING_WINDOW_HOURS = 24

# wakeup job id 前缀（按 event_id 区分）
WAKEUP_JOB_PREFIX = "hltv_wakeup_"

# 自适应轮询档位（next_minutes_until: 距离下一场比赛开始的分钟数）
# 注意：最低 3 分钟（在比赛临近/LIVE/赛后冷却期使用）
ADAPTIVE_INTERVAL_TABLE: list[tuple[int, int]] = [
    (60, 3),  # <= 1h
    (6 * 60, 30),  # <= 6h
    (24 * 60, 180),  # <= 24h
    (10**9, 360),  # > 24h
]

# 赛后冷却期（分钟）：_has_live_match 从 True 变 False 后，仍保持高频轮询的时长
POST_LIVE_GRACE_MINUTES = 30

# 比赛超时阈值（分钟）：预定时间已过但 HLTV 未标 LIVE，在此窗口内视为"已开赛"
OVERDUE_THRESHOLD_MINUTES = 30

EVENT_STATE = Literal["ONGOING", "UPCOMING", "NOT_ONGOING", "ENDED", "UNKNOWN"]

# 开赛提醒窗口（分钟）
REMINDER_WINDOW_MIN = 3   # 最晚：开赛前 3 分钟
REMINDER_WINDOW_MAX = 8   # 最早：开赛前 8 分钟
