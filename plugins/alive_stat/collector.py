"""
alive_stat 数据采集模块。
负责系统资源、进程状态、网络连通性的采集。
"""
import asyncio
import platform
from dataclasses import dataclass
from typing import Optional

import psutil

from .config import config

from ..utils.tools import get_logger

logger = get_logger("alive_stat.collector")


# ================= 数据类 =================

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
    processes: list[ProcessInfo] = None
    network: list[NetworkResult] = None

    def __post_init__(self):
        if self.processes is None:
            self.processes = []
        if self.network is None:
            self.network = []


# ================= 格式化工具 =================

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


# ================= 系统信息 =================

def get_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown"


def get_system_uptime() -> str:
    from .runtime import format_duration
    try:
        boot_time = psutil.boot_time()
        from datetime import datetime
        delta = datetime.now() - datetime.fromtimestamp(boot_time)
        return format_duration(delta)
    except Exception:
        return "N/A"


def get_resources() -> ResourceUsage:
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


# ================= 进程采集 =================

def _get_processes_psutil() -> list[ProcessInfo]:
    """
    按配置顺序匹配进程。
    已被前面条目统计过的 PID（含子进程）不会被后面重复计算。
    支持用 '|' 分隔多个关键字（OR 匹配）。
    """
    global_counted: set[int] = set()
    results = []

    for entry in config.monitored_processes:
        found = False
        mem_bytes = 0
        keywords_lower = [k.strip().lower() for k in entry.keyword.split("|")]

        matched_pids: set[int] = set()
        for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
            try:
                if proc.pid in global_counted:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                cmdline_lower = cmdline.lower()
                if any(kw in cmdline_lower for kw in keywords_lower):
                    matched_pids.add(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if matched_pids:
            found = True
            for pid in matched_pids:
                try:
                    p = psutil.Process(pid)
                    if pid not in global_counted:
                        global_counted.add(pid)
                        mem_bytes += p.memory_info().rss
                    for child in p.children(recursive=True):
                        if child.pid not in global_counted:
                            global_counted.add(child.pid)
                            try:
                                mem_bytes += child.memory_info().rss
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        results.append(ProcessInfo(
            name=entry.name, running=found, mem_bytes=mem_bytes,
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


async def get_processes() -> list[ProcessInfo]:
    results = _get_processes_psutil()
    for entry in config.docker_processes:
        running, mem_bytes = await _get_docker_process(entry.container)
        results.append(ProcessInfo(name=entry.name, running=running, mem_bytes=mem_bytes))
    return results


# ================= 网络检测 =================

async def _ping_host(host: str, timeout: int = 3) -> NetworkResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(timeout), host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
        if proc.returncode == 0:
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


async def get_network() -> list[NetworkResult]:
    results = await asyncio.gather(
        *[_ping_host(host) for host in config.ping_hosts]
    )
    return list(results)


# ================= 汇总 =================

async def collect_server_status() -> ServerStatus:
    resources = get_resources()
    processes = await get_processes()
    network = await get_network()

    return ServerStatus(
        hostname=platform.node(),
        cpu_model=get_cpu_model(),
        cpu_cores=psutil.cpu_count(logical=True) or 0,
        uptime=get_system_uptime(),
        resources=resources,
        processes=processes,
        network=network,
    )
