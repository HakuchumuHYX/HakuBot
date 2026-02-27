"""
alive_stat 运行时间追踪模块。
负责 HakuBot 和 Autochat 的运行时间统计、持久化。
"""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import config

from ..utils.tools import get_logger

logger = get_logger("alive_stat.runtime")

# ================= 数据目录 =================

DATA_DIR = Path() / "data" / "alive_stats"
DATA_FILE = DATA_DIR / "stats.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ================= 数据类 =================

@dataclass
class BotRuntime:
    name: str
    session_time: str
    session_since: str
    total_time: str
    total_since: str


# ================= 格式化工具 =================

def format_duration(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    days = total_seconds // 86400
    remain = total_seconds % 86400
    hours = remain // 3600
    remain %= 3600
    minutes = remain // 60
    seconds = remain % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def format_date(dt: datetime) -> str:
    return dt.strftime("%b. %d %Y")


# ================= 状态管理 =================

current_session_start = datetime.now()
total_saved_seconds = 0.0
first_record_time = current_session_start


def load_data():
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
            logger.exception(f"读取 alive 数据出错: {e}")


def save_data():
    now = datetime.now()
    current_uptime = (now - current_session_start).total_seconds()
    final_total = total_saved_seconds + current_uptime

    data = {
        "total_seconds": final_total,
        "first_record_timestamp": first_record_time.timestamp(),
        "session_start_timestamp": current_session_start.timestamp(),
        "last_save_time": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.exception(f"自动保存 alive 数据失败: {e}")


# 启动时加载
load_data()


# ================= Runtime 获取 =================

def get_hakubot_runtime(now: datetime) -> BotRuntime:
    current_delta = now - current_session_start
    total_sec = total_saved_seconds + current_delta.total_seconds()
    return BotRuntime(
        name="HakuBot",
        session_time=format_duration(current_delta),
        session_since=format_date(current_session_start),
        total_time=format_duration(timedelta(seconds=total_sec)),
        total_since=format_date(first_record_time),
    )


def get_autochat_runtime(now: datetime) -> BotRuntime:
    """读取 autochat 的 stats.json 来获取运行时间。"""
    default = BotRuntime(
        name="Autochat",
        session_time="N/A",
        session_since="N/A",
        total_time="N/A",
        total_since="N/A",
    )
    autochat_path = config.autochat_data_file
    if not autochat_path:
        return default

    autochat_file = Path(autochat_path)
    if not autochat_file.exists():
        return default

    try:
        with open(autochat_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        total_sec = data.get("total_seconds", 0.0)
        first_ts = data.get("first_record_timestamp")
        session_ts = data.get("session_start_timestamp")

        total_since_dt = datetime.fromtimestamp(first_ts) if first_ts else now
        total_str = format_duration(timedelta(seconds=total_sec))

        if session_ts:
            session_start = datetime.fromtimestamp(session_ts)
            session_delta = now - session_start
            session_str = format_duration(session_delta)
            session_since = format_date(session_start)
        else:
            session_str = "N/A"
            session_since = "N/A"

        return BotRuntime(
            name="Autochat",
            session_time=session_str,
            session_since=session_since,
            total_time=total_str,
            total_since=format_date(total_since_dt),
        )
    except Exception as e:
        logger.warning(f"读取 autochat stats 失败: {e}")
        return default
