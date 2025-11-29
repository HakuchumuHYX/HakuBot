import json
from datetime import datetime, timedelta
from pathlib import Path
from nonebot import on_command, require, get_driver
from nonebot.adapters import Bot, Event

# 引入定时任务支持
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ================= 配置与数据初始化 =================

# 设置数据存储路径
DATA_DIR = Path() / "data" / "alive_stats"
DATA_FILE = DATA_DIR / "stats.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# 全局变量
# current_session_start: 本次 Bot 启动的时间
current_session_start = datetime.now()
# total_saved_seconds: 启动前历史上已经累积的总秒数
total_saved_seconds = 0.0
# first_record_time: 第一次开始记录的时间
first_record_time = current_session_start


def load_data():
    """读取数据"""
    global total_saved_seconds, first_record_time
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                total_saved_seconds = data.get("total_seconds", 0.0)
                first_ts = data.get("first_record_timestamp")
                if first_ts:
                    first_record_time = datetime.fromtimestamp(first_ts)
        except Exception as e:
            print(f"读取 alive 数据出错: {e}")


# 启动时加载一次数据
load_data()


# ================= 核心保存逻辑 =================

# 设置定时任务：每 5 分钟执行一次 save_data
@scheduler.scheduled_job("interval", minutes=5, id="save_alive_stats")
def save_data():
    """
    计算并保存当前的总运行时间到文件。
    算法：文件中的总时间 = (历史累积时间) + (本次启动后的运行时长)
    """
    now = datetime.now()
    # 本次运行的时长
    current_uptime = (now - current_session_start).total_seconds()

    # 实时计算当前的总时长 (历史 + 本次)
    final_total = total_saved_seconds + current_uptime

    data = {
        "total_seconds": final_total,
        "first_record_timestamp": first_record_time.timestamp(),
        "last_save_time": now.strftime("%Y-%m-%d %H:%M:%S")  # 方便人工查看最后保存时间
    }

    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        # 这里可以用 nonebot.logger.error，为了简单演示用 print
        print(f"自动保存 alive 数据失败: {e}")


# ================= 生命周期钩子 =================

driver = get_driver()


@driver.on_shutdown
async def _():
    """Bot 关闭时强制保存一次"""
    save_data()


# ================= 辅助函数 =================

def format_duration(td: timedelta) -> str:
    """格式化 timedelta 为 Xd Xh Xmin Xs"""
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    remain_seconds = total_seconds % 86400
    hours = remain_seconds // 3600
    remain_seconds %= 3600
    minutes = remain_seconds // 60
    seconds = remain_seconds % 60

    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}min")
    parts.append(f"{seconds}s")

    return " ".join(parts)


def format_date(dt: datetime) -> str:
    """格式化日期为 Dec. 25 2025"""
    return dt.strftime("%b. %d %Y")


# ================= 响应器 =================

alive = on_command("alive-main", priority=5, block=True)


@alive.handle()
async def handle_alive(bot: Bot, event: Event):
    now = datetime.now()

    # 1. 计算本次运行时间
    current_uptime_delta = now - current_session_start
    current_str = format_duration(current_uptime_delta)
    current_since = format_date(current_session_start)

    # 2. 计算总运行时间 (内存中的历史 + 本次运行时间)
    # 注意：我们不读取文件，而是直接用内存计算，保证实时性
    total_uptime_seconds = total_saved_seconds + current_uptime_delta.total_seconds()
    total_uptime_delta = timedelta(seconds=total_uptime_seconds)

    total_str = format_duration(total_uptime_delta)
    total_since = format_date(first_record_time)

    msg = (
        f"当前运行时间：{current_str}, since {current_since}\n"
        f"总运行时间：{total_str}, since {total_since}"
    )

    await alive.finish(msg)
