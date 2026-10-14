from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from utils.rendering.engine import render_template

if TYPE_CHECKING:
    from plugins.alive_stat.runtime import BotRuntime
    from plugins.alive_stat.collector import ServerStatus

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    elif b < 1024**2:
        return f"{b / 1024:.1f}K"
    elif b < 1024**3:
        return f"{b / 1024**2:.1f}M"
    else:
        return f"{b / 1024**3:.1f}G"


def _resource(name: str, percent: float, detail: str) -> dict:
    # 阈值与 webconsole 的负载条一致
    level = "critical" if percent >= 90 else "high" if percent >= 80 else ""
    return {"name": name, "percent": percent, "detail": detail, "level": level}


async def draw_alive_card(
    hakubot_runtime: "BotRuntime",
    autochat_runtime: "BotRuntime",
    server: "ServerStatus",
    watermark: str,
    is_night: bool = False,
) -> bytes:
    res = server.resources
    has_swap = res.swap_total > 0
    resources = [
        _resource("CPU", res.cpu_percent, ""),
        _resource(
            "内存",
            res.mem_percent,
            f"{_format_bytes(res.mem_used)}/{_format_bytes(res.mem_total)}",
        ),
        _resource(
            "Swap",
            res.swap_percent if has_swap else 0,
            f"{_format_bytes(res.swap_used)}/{_format_bytes(res.swap_total)}"
            if has_swap
            else "N/A",
        ),
        _resource(
            "磁盘",
            res.disk_percent,
            f"{_format_bytes(res.disk_used)}/{_format_bytes(res.disk_total)}",
        ),
    ]
    processes = [
        {
            "name": proc.name,
            "running": proc.running,
            "mem": _format_bytes(proc.mem_bytes) if proc.running else "-",
        }
        # 停止的进程排在最前面，一眼就能看到
        for proc in sorted(server.processes or [], key=lambda p: p.running)
    ]
    # watermark 第一行是品牌，第二行是生成时间
    lines = [ln.strip() for ln in watermark.splitlines() if ln.strip()] + ["", ""]

    return await render_template(
        TEMPLATE_DIR,
        "status.html",
        width=900,
        dark=is_night,
        bots=[hakubot_runtime, autochat_runtime],
        server=server,
        resources=resources,
        processes=processes,
        networks=server.network or [],
        brand=lines[0],
        generated=lines[1],
    )
