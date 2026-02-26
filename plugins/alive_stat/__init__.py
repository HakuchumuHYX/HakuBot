"""
HakuBot alive_stat — 统一处理 alive 命令。
显示 HakuBot + autochat 运行时间、服务器状态、进程监控、网络连通性。
"""
import asyncio
import json
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import psutil
from nonebot import on_command, require, get_driver
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..utils.tools import get_logger

logger = get_logger("alive_stat")

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.adapters.onebot.v11 import GroupMessageEvent

try:
    _driver = get_driver()
except ValueError:
    _driver = None

if _driver is not None:
    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler
else:
    scheduler = None  # type: ignore

from . import drawer

try:
    from ..plugin_manager.enable import is_plugin_enabled  # type: ignore
except Exception:
    is_plugin_enabled = None  # type: ignore

# ================= 配置 =================

DATA_DIR = Path() / "data" / "alive_stats"
DATA_FILE = DATA_DIR / "stats.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# autochat 数据文件的绝对路径
AUTOCHAT_DATA_FILE = Path("/opt/HakuBot-autochat/data/alive_stats/stats.json")

# 要监控的进程列表：(显示名, cmdline 匹配关键字)
# NapCat 通过 Docker 运行，单独处理
MONITORED_PROCESSES = [
    ("HakuBot", "/opt/HakuBot/.venv/"),
    ("HakuBot-autochat", "/opt/HakuBot-autochat/.venv/"),
    ("OneBotFilter", "OneBotFilter"),
    ("HarukiBot", "HarukiClient"),
    ("MySQL", "mysqld"),
]

DOCKER_PROCESSES = [
    ("NapCat", "napcat"),  # (显示名, 容器名)
    ("CLIProxyAPI", "cli-proxy-api"),
]

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


load_data()


# ================= 定时保存 =================

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


if scheduler is not None:
    scheduler.scheduled_job("interval", minutes=5, id="save_alive_stats")(save_data)

if _driver is not None:
    @_driver.on_shutdown
    async def _():
        save_data()


# ================= 辅助函数 =================

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


def format_bytes(b: int) -> str:
    """将字节数格式化为人类可读的字符串。"""
    if b < 1024:
        return f"{b}B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f}K"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f}M"
    else:
        return f"{b / 1024 ** 3:.1f}G"


# ================= 数据采集 =================

@dataclass
class BotRuntime:
    name: str
    session_time: str
    session_since: str
    total_time: str
    total_since: str


@dataclass
class ResourceUsage:
    cpu_percent: float
    mem_used: int
    mem_total: int
    mem_percent: float
    disk_used: int
    disk_total: int
    disk_percent: float


@dataclass
class ProcessInfo:
    name: str
    running: bool
    mem_bytes: int  # RSS（含子进程），0 表示未运行


@dataclass
class NetworkResult:
    host: str
    reachable: bool
    latency_ms: Optional[float]  # None 表示不可达


@dataclass
class ServerStatus:
    hostname: str
    cpu_model: str
    cpu_cores: int
    uptime: str
    resources: ResourceUsage
    processes: list["ProcessInfo"] = None
    network: list["NetworkResult"] = None

    def __post_init__(self):
        if self.processes is None:
            self.processes = []
        if self.network is None:
            self.network = []


def _get_hakubot_runtime(now: datetime) -> BotRuntime:
    current_delta = now - current_session_start
    total_sec = total_saved_seconds + current_delta.total_seconds()
    return BotRuntime(
        name="HakuBot",
        session_time=format_duration(current_delta),
        session_since=format_date(current_session_start),
        total_time=format_duration(timedelta(seconds=total_sec)),
        total_since=format_date(first_record_time),
    )


def _get_autochat_runtime(now: datetime) -> BotRuntime:
    """读取 autochat 的 stats.json 来获取运行时间。"""
    default = BotRuntime(
        name="Autochat",
        session_time="N/A",
        session_since="N/A",
        total_time="N/A",
        total_since="N/A",
    )
    if not AUTOCHAT_DATA_FILE.exists():
        return default
    try:
        with open(AUTOCHAT_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        total_sec = data.get("total_seconds", 0.0)
        first_ts = data.get("first_record_timestamp")
        session_ts = data.get("session_start_timestamp")
        last_save = data.get("last_save_time", "")

        # total
        total_since_dt = datetime.fromtimestamp(first_ts) if first_ts else now
        total_str = format_duration(timedelta(seconds=total_sec))

        # session: 用 session_start_timestamp 计算
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


def _get_cpu_model() -> str:
    """尝试获取 CPU 型号名称。"""
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"


def _get_system_uptime() -> str:
    """获取系统运行时间。"""
    try:
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        delta = datetime.now() - boot_time
        return format_duration(delta)
    except Exception:
        return "N/A"


def _get_resources() -> ResourceUsage:
    cpu_percent = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return ResourceUsage(
        cpu_percent=cpu_percent,
        mem_used=mem.used,
        mem_total=mem.total,
        mem_percent=mem.percent,
        disk_used=disk.used,
        disk_total=disk.total,
        disk_percent=disk.percent,
    )


def _get_processes_psutil() -> list[ProcessInfo]:
    results = []
    for display_name, keyword in MONITORED_PROCESSES:
        found = False
        mem_bytes = 0
        keyword_lower = keyword.lower()
        # 收集所有匹配的进程 PID
        matched_pids: set[int] = set()
        for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if keyword_lower in cmdline.lower():
                    matched_pids.add(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if matched_pids:
            found = True
            counted_pids: set[int] = set()
            for pid in matched_pids:
                try:
                    p = psutil.Process(pid)
                    if pid not in counted_pids:
                        counted_pids.add(pid)
                        mem_bytes += p.memory_info().rss
                    for child in p.children(recursive=True):
                        if child.pid not in counted_pids and child.pid not in matched_pids:
                            counted_pids.add(child.pid)
                            try:
                                mem_bytes += child.memory_info().rss
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        results.append(ProcessInfo(
            name=display_name, running=found, mem_bytes=mem_bytes,
        ))
    return results


async def _get_docker_process(container_name: str) -> tuple[bool, int]:
    """检查 Docker 容器状态和内存占用。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format", "{{.State.Running}}", container_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        running = stdout.decode().strip().lower() == "true"
        if not running:
            return False, 0

        proc2 = await asyncio.create_subprocess_exec(
            "docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=5)
        mem_str = stdout2.decode().strip().split("/")[0].strip()
        mem_bytes = _parse_docker_mem(mem_str)
        return True, mem_bytes
    except Exception:
        return False, 0


def _parse_docker_mem(s: str) -> int:
    """解析 docker stats 的内存字符串，如 '120.5MiB' -> bytes。"""
    s = s.strip()
    try:
        if s.endswith("GiB"):
            return int(float(s[:-3]) * 1024 ** 3)
        elif s.endswith("MiB"):
            return int(float(s[:-3]) * 1024 ** 2)
        elif s.endswith("KiB"):
            return int(float(s[:-3]) * 1024)
        elif s.endswith("B"):
            return int(float(s[:-1]))
    except (ValueError, IndexError):
        pass
    return 0


async def _get_processes() -> list[ProcessInfo]:
    results = _get_processes_psutil()
    for display_name, container_name in DOCKER_PROCESSES:
        running, mem_bytes = await _get_docker_process(container_name)
        results.append(ProcessInfo(name=display_name, running=running, mem_bytes=mem_bytes))
    return results


async def _ping_host(host: str, timeout: int = 3) -> NetworkResult:
    """异步 ping 一个主机。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout), host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
        if proc.returncode == 0:
            # 解析延迟
            output = stdout.decode("utf-8", errors="ignore")
            latency = None
            for line in output.splitlines():
                if "time=" in line:
                    try:
                        t_part = line.split("time=")[1].split()[0]
                        latency = float(t_part.replace("ms", ""))
                    except (IndexError, ValueError):
                        pass
                    break
            return NetworkResult(host=host, reachable=True, latency_ms=latency)
        else:
            return NetworkResult(host=host, reachable=False, latency_ms=None)
    except Exception:
        return NetworkResult(host=host, reachable=False, latency_ms=None)


async def _get_network() -> list[NetworkResult]:
    results = await asyncio.gather(
        _ping_host("baidu.com"),
        _ping_host("google.com"),
    )
    return list(results)


async def collect_server_status() -> ServerStatus:
    """采集完整的服务器状态。"""
    resources = _get_resources()
    processes = await _get_processes()
    network = await _get_network()

    return ServerStatus(
        hostname=platform.node(),
        cpu_model=_get_cpu_model(),
        cpu_cores=psutil.cpu_count(logical=True) or 0,
        uptime=_get_system_uptime(),
        resources=resources,
        processes=processes,
        network=network,
    )


# ================= 响应器 =================

alive = on_command("alive", priority=5, block=True)


@alive.handle()
async def handle_alive(bot: Bot, event: Event, args: Message = CommandArg()):
    user_id = str(event.get_user_id())
    if isinstance(event, GroupMessageEvent) and is_plugin_enabled is not None:
        if not is_plugin_enabled("alive_stat", str(event.group_id), user_id):
            await alive.finish()

    now = datetime.now()

    # 日夜模式
    arg_text = args.extract_plain_text().strip().lower()
    if "night" in arg_text:
        is_night = True
    elif "day" in arg_text:
        is_night = False
    else:
        hour = now.hour
        is_night = not (6 <= hour < 18)

    # 采集数据
    hakubot_rt = _get_hakubot_runtime(now)
    autochat_rt = _get_autochat_runtime(now)
    server = await collect_server_status()

    # 水印
    watermark_time = now.strftime("%H:%M:%S %b. %d %Y")
    watermark = f"Generated by HakuBot\nGenerated at {watermark_time}"

    # 绘图
    image_bytes = await drawer.draw_alive_card(
        hakubot_runtime=hakubot_rt,
        autochat_runtime=autochat_rt,
        server=server,
        watermark=watermark,
        is_night=is_night,
    )

    await alive.finish(MessageSegment.image(image_bytes))
