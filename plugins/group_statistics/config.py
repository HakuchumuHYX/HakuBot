import os
from typing import Set, Dict, List, Tuple

# 数据文件路径
DATA_DIR = "data/group_statistics"
STATS_FILE = os.path.join(DATA_DIR, "stats.json")

# 确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)

# 消息阶梯配置
MESSAGE_THRESHOLDS = {
    2000: "今天大家聊得很热闹呢！明天也继续努力吧！",
    1000: "今天大家也很活跃呢！明天也继续努力吧！",
    500: "今天大家聊得也很多呢，明天也继续努力吧！",
    100: "今天没什么干劲呢，明天再继续努力吧！"
}

DEFAULT_THRESHOLD_TEXT = "今天大家聊得不是很多呢……但是没问题，ATRI会一直在这里等着哦！"

# 其他配置项
TOP_N_USERS = 5  # 显示前N名用户
MESSAGE_HANDLER_PRIORITY = 1  # 消息处理器优先级
STAT_COMMAND_PRIORITY = 10  # 统计命令优先级