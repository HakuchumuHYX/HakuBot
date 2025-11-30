import json
from datetime import datetime, timedelta
from pathlib import Path
from nonebot import on_command, require, get_driver
from nonebot.adapters import Bot, Event

# 请根据适配器修改 (例如 OneBot V11)
from nonebot.adapters.onebot.v11 import MessageSegment

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# 导入同目录下的绘图模块
from . import drawer

# ================= 配置与数据初始化 =================

DATA_DIR = Path() / "data" / "alive_stats"
DATA_FILE = DATA_DIR / "stats.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

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
            print(f"读取 alive 数据出错: {e}")


load_data()


# ================= 定时保存逻辑 =================

@scheduler.scheduled_job("interval", minutes=5, id="save_alive_stats")
def save_data():
    now = datetime.now()
    current_uptime = (now - current_session_start).total_seconds()
    final_total = total_saved_seconds + current_uptime

    data = {
        "total_seconds": final_total,
        "first_record_timestamp": first_record_time.timestamp(),
        "last_save_time": now.strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"自动保存 alive 数据失败: {e}")


driver = get_driver()


@driver.on_shutdown
async def _():
    save_data()


# ================= 辅助函数 =================

def format_duration(td: timedelta) -> str:
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
    return dt.strftime("%b. %d %Y")


# ================= 响应器 =================

alive = on_command("alive-main", priority=5, block=True)


@alive.handle()
async def handle_alive(bot: Bot, event: Event):
    now = datetime.now()

    # 1. 计算时间数据
    current_uptime_delta = now - current_session_start
    current_str = format_duration(current_uptime_delta)
    current_since = format_date(current_session_start)

    total_uptime_seconds = total_saved_seconds + current_uptime_delta.total_seconds()
    total_uptime_delta = timedelta(seconds=total_uptime_seconds)
    total_str = format_duration(total_uptime_delta)
    total_since = format_date(first_record_time)

    # 2. 准备水印内容
    # 格式化当前时间
    watermark_time = now.strftime("%H:%M:%S %b. %d %Y")

    # 在这里修改你的自定义文本
    my_custom_text = "HakuBot Powered by Hakuchumu"

    # 拼接完整水印，使用 \n 换行
    full_watermark = f"{my_custom_text}\nGenerated at {watermark_time}"

    # 3. 调用 Drawer 生成图片
    image_bytes = drawer.draw_alive_card(
        current_time=current_str,
        current_since=current_since,
        total_time=total_str,
        total_since=total_since,
        watermark=full_watermark
    )

    # 4. 发送
    await alive.finish(MessageSegment.image(image_bytes))
