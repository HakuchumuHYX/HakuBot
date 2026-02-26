from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils.draw.painter import LinearGradient, Painter
from ..utils.draw.plot import (
    Canvas,
    FillBg,
    HSplit,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
    Widget,
)

if TYPE_CHECKING:
    from . import BotRuntime, ServerStatus, ProcessInfo, NetworkResult

# ================= 资源配置 =================
PLUGIN_DIR = Path(__file__).parent
RES_DIR = PLUGIN_DIR / "resources"
RES_DIR.mkdir(parents=True, exist_ok=True)
FONT_FILE = RES_DIR / "font.ttf"

DEFAULT_WIDTH = 900


@dataclass(frozen=True)
class Theme:
    canvas_g1: tuple[int, int, int, int]
    canvas_g2: tuple[int, int, int, int]
    card_bg: tuple[int, int, int, int]
    card_border: tuple[int, int, int, int]
    section_bg: tuple[int, int, int, int]
    section_border: tuple[int, int, int, int]
    block_bg: tuple[int, int, int, int]
    block_border: tuple[int, int, int, int]
    text_main: tuple[int, int, int, int]
    text_sub: tuple[int, int, int, int]
    text_muted: tuple[int, int, int, int]
    accent: tuple[int, int, int, int]
    bar_bg: tuple[int, int, int, int]
    bar_fill: tuple[int, int, int, int]
    bar_fill_warn: tuple[int, int, int, int]
    bar_fill_danger: tuple[int, int, int, int]
    green: tuple[int, int, int, int]
    red: tuple[int, int, int, int]


THEME_DAY = Theme(
    canvas_g1=(224, 244, 255, 255),
    canvas_g2=(252, 254, 255, 255),
    card_bg=(255, 255, 255, 235),
    card_border=(170, 210, 235, 255),
    section_bg=(245, 250, 255, 255),
    section_border=(200, 225, 240, 255),
    block_bg=(242, 250, 255, 255),
    block_border=(200, 230, 245, 255),
    text_main=(25, 55, 75, 255),
    text_sub=(80, 120, 140, 255),
    text_muted=(120, 150, 165, 255),
    accent=(35, 125, 175, 255),
    bar_bg=(220, 235, 245, 255),
    bar_fill=(70, 160, 210, 255),
    bar_fill_warn=(230, 170, 50, 255),
    bar_fill_danger=(220, 80, 60, 255),
    green=(50, 170, 90, 255),
    red=(220, 70, 60, 255),
)

THEME_NIGHT = Theme(
    canvas_g1=(18, 24, 36, 255),
    canvas_g2=(34, 46, 68, 255),
    card_bg=(26, 32, 44, 240),
    card_border=(88, 118, 160, 255),
    section_bg=(30, 38, 54, 255),
    section_border=(60, 80, 110, 255),
    block_bg=(32, 40, 56, 255),
    block_border=(70, 92, 128, 255),
    text_main=(236, 244, 252, 255),
    text_sub=(168, 190, 212, 255),
    text_muted=(120, 140, 160, 255),
    accent=(120, 230, 210, 255),
    bar_bg=(40, 50, 70, 255),
    bar_fill=(80, 190, 170, 255),
    bar_fill_warn=(220, 180, 60, 255),
    bar_fill_danger=(220, 80, 60, 255),
    green=(80, 210, 130, 255),
    red=(240, 90, 80, 255),
)


def _font() -> str:
    if FONT_FILE.exists():
        return str(FONT_FILE)
    return "SourceHanSansCN-Regular"


def _split_watermark(wm: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in str(wm).splitlines() if ln.strip()]
    if not lines:
        return ("", "")
    if len(lines) == 1:
        return (lines[0], "")
    return (lines[0], lines[1])


def _bar_color(theme: Theme, percent: float) -> tuple[int, int, int, int]:
    if percent >= 90:
        return theme.bar_fill_danger
    elif percent >= 75:
        return theme.bar_fill_warn
    return theme.bar_fill


def _format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f}K"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f}M"
    else:
        return f"{b / 1024 ** 3:.1f}G"


# ================= Section: 标题 =================

def _section_label(text: str, theme: Theme, font: str, w: int) -> TextBox:
    style = TextStyle(font=font, size=18, color=theme.text_muted)
    return (
        TextBox(text, style=style, wrap=False)
        .set_w(w)
        .set_content_align("l")
        .set_padding(0)
    )


# ================= Section: Bot Runtime =================

def _build_runtime_block(
    rt: "BotRuntime", theme: Theme, font: str, width: int
) -> VSplit:
    name_style = TextStyle(font=font, size=18, color=theme.text_sub)
    value_style = TextStyle(font=font, size=26, color=theme.accent)
    label_style = TextStyle(font=font, size=15, color=theme.text_muted)
    since_style = TextStyle(font=font, size=14, color=theme.text_muted)

    items = [
        TextBox(rt.name, style=name_style, wrap=False)
        .set_w(width).set_content_align("l").set_padding(0),
        Spacer(1, 8),
        # Session
        TextBox("Session", style=label_style, wrap=False)
        .set_w(width).set_content_align("l").set_padding(0),
        TextBox(rt.session_time, style=value_style, wrap=True, line_count=2,
                line_sep=2, overflow="shrink", use_real_line_count=True)
        .set_w(width).set_content_align("l").set_padding(0),
        TextBox(f"Since {rt.session_since}", style=since_style, wrap=False, overflow="shrink")
        .set_w(width).set_content_align("l").set_padding(0),
        Spacer(1, 14),
        # Total
        TextBox("Total", style=label_style, wrap=False)
        .set_w(width).set_content_align("l").set_padding(0),
        TextBox(rt.total_time, style=value_style, wrap=True, line_count=2,
                line_sep=2, overflow="shrink", use_real_line_count=True)
        .set_w(width).set_content_align("l").set_padding(0),
        TextBox(f"Since {rt.total_since}", style=since_style, wrap=False, overflow="shrink")
        .set_w(width).set_content_align("l").set_padding(0),
    ]

    return (
        VSplit(items=items, sep=0, item_size_mode="fixed", item_align="l")
        .set_w(width)
        .set_padding((18, 16))
        .set_bg(RoundRectBg(fill=theme.block_bg, radius=18, stroke=theme.block_border, stroke_width=2))
    )


def _build_runtime_section(
    hakubot: "BotRuntime", autochat: "BotRuntime",
    theme: Theme, font: str, content_w: int
) -> VSplit:
    block_sep = 16
    block_w = (content_w - block_sep) // 2

    haku_block = _build_runtime_block(hakubot, theme, font, block_w)
    auto_block = _build_runtime_block(autochat, theme, font, block_w)

    row = (
        HSplit(items=[haku_block, auto_block], sep=block_sep,
               item_size_mode="fixed", item_align="t")
        .set_w(content_w).set_padding(0)
    )

    return (
        VSplit(items=[
            _section_label("── Bot Runtime ──", theme, font, content_w),
            Spacer(1, 12),
            row,
        ], sep=0, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )


# ================= Section: Server Info =================

def _build_server_info_section(
    server: "ServerStatus", theme: Theme, font: str, content_w: int
) -> VSplit:
    info_style = TextStyle(font=font, size=16, color=theme.text_sub)
    muted_style = TextStyle(font=font, size=14, color=theme.text_muted)

    # 截断过长的 CPU 型号
    cpu_model = server.cpu_model
    if len(cpu_model) > 50:
        cpu_model = cpu_model[:47] + "..."

    items = [
        _section_label("── Server ──", theme, font, content_w),
        Spacer(1, 10),
        TextBox(
            f"{server.hostname}  |  Uptime: {server.uptime}",
            style=info_style, wrap=False, overflow="shrink"
        ).set_w(content_w).set_content_align("l").set_padding(0),
        Spacer(1, 6),
        TextBox(
            f"CPU: {cpu_model} ({server.cpu_cores} cores)",
            style=muted_style, wrap=True, line_count=2, line_sep=2,
            overflow="shrink", use_real_line_count=True
        ).set_w(content_w).set_content_align("l").set_padding(0),
    ]

    return (
        VSplit(items=items, sep=0, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )


# ================= Section: Resources (with progress bars) =================

class _ProgressBarWidget(Widget):
    """自定义进度条 Widget，用 Painter 直接绘制圆角矩形。"""

    def __init__(self, percent: float, bar_bg: tuple, bar_fill: tuple,
                 width: int, height: int = 18, radius: int = 9):
        super().__init__()
        self._percent = max(0.0, min(100.0, percent))
        self._bar_bg = bar_bg
        self._bar_fill = bar_fill
        self._bar_w = width
        self._bar_h = height
        self._radius = radius
        self.set_w(width)
        self.set_h(height)

    def _get_content_size(self):
        return (self._bar_w, self._bar_h)

    def _draw_content(self, p: Painter):
        w, h = self._bar_w, self._bar_h
        r = self._radius
        # 背景
        p.roundrect((0, 0), (w, h), fill=self._bar_bg, radius=r, stroke=None, stroke_width=0)
        # 填充
        fill_w = max(int(w * self._percent / 100.0), r * 2) if self._percent > 0 else 0
        if fill_w > 0:
            p.roundrect((0, 0), (fill_w, h), fill=self._bar_fill, radius=r, stroke=None, stroke_width=0)


def _build_resource_row(
    label: str, percent: float, detail: str,
    theme: Theme, font: str, content_w: int
) -> HSplit:
    label_style = TextStyle(font=font, size=16, color=theme.text_sub)
    pct_style = TextStyle(font=font, size=16, color=theme.text_main)
    detail_style = TextStyle(font=font, size=14, color=theme.text_muted)

    label_w = 60
    pct_w = 60
    detail_w = 120
    bar_w = content_w - label_w - pct_w - detail_w - 30  # 30 for gaps

    bar_color = _bar_color(theme, percent)
    bar = _ProgressBarWidget(percent, theme.bar_bg, bar_color, width=max(bar_w, 80), height=16, radius=8)

    items = [
        TextBox(label, style=label_style, wrap=False)
        .set_w(label_w).set_content_align("l").set_padding(0),
        bar,
        TextBox(f"{percent:.1f}%", style=pct_style, wrap=False)
        .set_w(pct_w).set_content_align("r").set_padding(0),
        TextBox(detail, style=detail_style, wrap=False, overflow="shrink")
        .set_w(detail_w).set_content_align("r").set_padding(0),
    ]

    return (
        HSplit(items=items, sep=8, item_size_mode="fixed", item_align="c")
        .set_w(content_w).set_padding(0)
    )


def _build_resources_section(
    server: "ServerStatus", theme: Theme, font: str, content_w: int
) -> VSplit:
    res = server.resources

    cpu_row = _build_resource_row(
        "CPU", res.cpu_percent, "",
        theme, font, content_w,
    )
    mem_detail = f"{_format_bytes(res.mem_used)}/{_format_bytes(res.mem_total)}"
    mem_row = _build_resource_row(
        "Mem", res.mem_percent, mem_detail,
        theme, font, content_w,
    )
    disk_detail = f"{_format_bytes(res.disk_used)}/{_format_bytes(res.disk_total)}"
    disk_row = _build_resource_row(
        "Disk", res.disk_percent, disk_detail,
        theme, font, content_w,
    )

    return (
        VSplit(items=[
            _section_label("── Resources ──", theme, font, content_w),
            Spacer(1, 10),
            cpu_row,
            Spacer(1, 10),
            mem_row,
            Spacer(1, 10),
            disk_row,
        ], sep=0, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )


# ================= Section: Processes =================

def _build_process_row(
    proc: "ProcessInfo", theme: Theme, font: str, content_w: int
) -> HSplit:
    name_style = TextStyle(font=font, size=16, color=theme.text_sub)
    status_color = theme.green if proc.running else theme.red
    status_style = TextStyle(font=font, size=16, color=status_color)
    mem_style = TextStyle(font=font, size=14, color=theme.text_muted)

    status_text = "Running" if proc.running else "Stopped"
    mem_text = _format_bytes(proc.mem_bytes) if proc.running else "-"

    dot_style = TextStyle(font=font, size=16, color=status_color)

    name_w = 200
    status_w = 100
    mem_w = 100

    items = [
        TextBox("●", style=dot_style, wrap=False)
        .set_w(20).set_content_align("c").set_padding(0),
        TextBox(proc.name, style=name_style, wrap=False, overflow="shrink")
        .set_w(name_w).set_content_align("l").set_padding(0),
        TextBox(status_text, style=status_style, wrap=False)
        .set_w(status_w).set_content_align("l").set_padding(0),
        TextBox(mem_text, style=mem_style, wrap=False)
        .set_w(mem_w).set_content_align("r").set_padding(0),
    ]

    return (
        HSplit(items=items, sep=6, item_size_mode="fixed", item_align="c")
        .set_w(content_w).set_padding(0)
    )


def _build_processes_section(
    server: "ServerStatus", theme: Theme, font: str, content_w: int
) -> VSplit:
    items: list[Widget] = [
        _section_label("── Processes ──", theme, font, content_w),
        Spacer(1, 10),
    ]
    for i, proc in enumerate(server.processes):
        if i > 0:
            items.append(Spacer(1, 8))
        items.append(_build_process_row(proc, theme, font, content_w))

    return (
        VSplit(items=items, sep=0, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )


# ================= Section: Network =================

def _build_network_section(
    server: "ServerStatus", theme: Theme, font: str, content_w: int
) -> VSplit:
    parts = []
    for nr in server.network:
        if nr.reachable:
            lat = f"{nr.latency_ms:.0f}ms" if nr.latency_ms is not None else "ok"
            parts.append(f"{nr.host}  ✓ {lat}")
        else:
            parts.append(f"{nr.host}  ✗ timeout")

    net_text = "  |  ".join(parts) if parts else "N/A"

    info_style = TextStyle(font=font, size=16, color=theme.text_sub)

    return (
        VSplit(items=[
            _section_label("── Network ──", theme, font, content_w),
            Spacer(1, 10),
            TextBox(net_text, style=info_style, wrap=False, overflow="shrink")
            .set_w(content_w).set_content_align("l").set_padding(0),
        ], sep=0, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )


# ================= 主绘图函数 =================

async def draw_alive_card(
    hakubot_runtime: "BotRuntime",
    autochat_runtime: "BotRuntime",
    server: "ServerStatus",
    watermark: str,
    is_night: bool = False,
    width: int = DEFAULT_WIDTH,
) -> bytes:
    theme = THEME_NIGHT if is_night else THEME_DAY
    font = _font()

    brand_line, generated_line = _split_watermark(watermark)

    outer_margin = 28
    card_padding = 32
    section_sep = 26

    content_w = width - outer_margin * 2 - card_padding * 2

    # ---- Header ----
    title_style = TextStyle(font=font, size=30, color=theme.text_main, use_shadow=False)
    subtitle_style = TextStyle(font=font, size=15, color=theme.text_sub)

    header_items = [
        TextBox("HakuBot Server Status", style=title_style, wrap=False)
        .set_w(content_w).set_content_align("l").set_padding(0),
    ]
    if generated_line:
        header_items.append(
            TextBox(generated_line, style=subtitle_style, wrap=False, overflow="shrink")
            .set_w(content_w).set_content_align("l").set_padding(0)
        )
    header = (
        VSplit(items=header_items, sep=6, item_size_mode="fixed", item_align="l")
        .set_w(content_w).set_padding(0)
    )

    # ---- Sections ----
    runtime_sec = _build_runtime_section(hakubot_runtime, autochat_runtime, theme, font, content_w)
    server_sec = _build_server_info_section(server, theme, font, content_w)
    resources_sec = _build_resources_section(server, theme, font, content_w)
    processes_sec = _build_processes_section(server, theme, font, content_w)
    network_sec = _build_network_section(server, theme, font, content_w)

    # ---- Footer ----
    watermark_style = TextStyle(font=font, size=14, color=theme.text_muted)
    footer_items = []
    if brand_line:
        footer_items.append(
            TextBox(brand_line, style=watermark_style, wrap=False, overflow="shrink")
            .set_w(content_w).set_content_align("r").set_padding(0)
        )
    footer = (
        VSplit(items=footer_items, sep=0, item_size_mode="fixed", item_align="r")
        .set_w(content_w).set_padding(0)
    )

    # ---- Compose card ----
    card_items = [
        header,
        Spacer(1, section_sep),
        runtime_sec,
        Spacer(1, section_sep),
        server_sec,
        Spacer(1, section_sep),
        resources_sec,
        Spacer(1, section_sep),
        processes_sec,
        Spacer(1, section_sep),
        network_sec,
        Spacer(1, 14),
        footer,
    ]

    card = (
        VSplit(items=card_items, sep=0, item_size_mode="fixed", item_align="c")
        .set_w(width - outer_margin * 2)
        .set_padding((card_padding, card_padding))
        .set_margin(outer_margin)
        .set_bg(RoundRectBg(fill=theme.card_bg, radius=28, stroke=theme.card_border, stroke_width=2))
    )

    # ---- Canvas ----
    canvas_bg = FillBg(
        LinearGradient(
            theme.canvas_g1, theme.canvas_g2,
            (0.0, 0.0), (1.0, 1.0),
            method="seperate",
        )
    )
    canvas = Canvas(w=width, h=None, bg=canvas_bg).set_items([card]).set_content_align("c")

    def _decorations(widget, p):
        p.roundrect(
            (outer_margin - 10, outer_margin - 10), (180, 120),
            fill=(theme.accent[0], theme.accent[1], theme.accent[2], 28),
            radius=60, stroke=None, stroke_width=0,
        )
        p.roundrect(
            (p.w - 220, p.h - 160), (200, 140),
            fill=(theme.accent[0], theme.accent[1], theme.accent[2], 22),
            radius=70, stroke=None, stroke_width=0,
        )

    canvas.add_draw_func(_decorations)

    img = await canvas.get_img()
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
