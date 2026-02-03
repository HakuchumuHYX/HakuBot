"""
scheduler 常量集中定义
"""

from __future__ import annotations

from typing import Literal

JOB_ID = "hltv_check"
DEFAULT_INTERVAL_MINUTES = 5

# start_date 前多少小时进入 UPCOMING（仅在这个窗口内才恢复轮询）
UPCOMING_WINDOW_HOURS = 24

# wakeup job id 前缀（按 event_id 区分）
WAKEUP_JOB_PREFIX = "hltv_wakeup_"

# 自适应轮询档位（next_minutes_until: 距离下一场比赛开始的分钟数）
# 注意：最低仍然是 5 分钟（不会更频繁，降低 403 风险）
ADAPTIVE_INTERVAL_TABLE: list[tuple[int, int]] = [
    (60, 5),  # <= 1h
    (6 * 60, 15),  # <= 6h
    (24 * 60, 60),  # <= 24h
    (10**9, 180),  # > 24h
]

EVENT_STATE = Literal["ONGOING", "UPCOMING", "NOT_ONGOING", "ENDED", "UNKNOWN"]
