from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from ..utils.draw.painter import LinearGradient
from ..utils.draw.plot import (
    Canvas,
    FillBg,
    HSplit,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)

# ================= 资源配置 =================
PLUGIN_DIR = Path(__file__).parent
RES_DIR = PLUGIN_DIR / "resources"
RES_DIR.mkdir(parents=True, exist_ok=True)

# 插件自带字体（迁移友好：复制 alive_stat 即可带走）
FONT_FILE = RES_DIR / "font.ttf"

# 默认宽度（高度自适应）
DEFAULT_WIDTH = 800


@dataclass(frozen=True)
class AliveCardTheme:
    # canvas background gradient
    canvas_g1: tuple[int, int, int, int]
    canvas_g2: tuple[int, int, int, int]

    # main card
    card_bg: tuple[int, int, int, int]
    card_border: tuple[int, int, int, int]

    # stat blocks
    block_bg: tuple[int, int, int, int]
    block_border: tuple[int, int, int, int]

    # text
    text_main: tuple[int, int, int, int]
    text_sub: tuple[int, int, int, int]
    text_muted: tuple[int, int, int, int]

    # accent (for big numbers)
    accent: tuple[int, int, int, int]


THEME_DAY = AliveCardTheme(
    canvas_g1=(224, 244, 255, 255),
    canvas_g2=(252, 254, 255, 255),
    card_bg=(255, 255, 255, 235),
    card_border=(170, 210, 235, 255),
    block_bg=(242, 250, 255, 255),
    block_border=(200, 230, 245, 255),
    text_main=(25, 55, 75, 255),
    text_sub=(80, 120, 140, 255),
    text_muted=(120, 150, 165, 255),
    accent=(35, 125, 175, 255),
)

THEME_NIGHT = AliveCardTheme(
    canvas_g1=(18, 24, 36, 255),
    canvas_g2=(34, 46, 68, 255),
    card_bg=(26, 32, 44, 240),
    card_border=(88, 118, 160, 255),
    block_bg=(32, 40, 56, 255),
    block_border=(70, 92, 128, 255),
    text_main=(236, 244, 252, 255),
    text_sub=(168, 190, 212, 255),
    text_muted=(120, 140, 160, 255),
    accent=(120, 230, 210, 255),
)


def _pick_font() -> str:
    """优先使用插件自带字体；如果不存在则交给 painter 默认字体 fallback。"""
    if FONT_FILE.exists():
        # 传入路径字符串，painter.get_font 会优先按路径加载
        return str(FONT_FILE)
    return "SourceHanSansCN-Regular"


def _split_watermark(watermark: str) -> tuple[str, str]:
    """将 watermark 拆成 (brand_line, generated_line)。
    """
    lines = [ln.strip() for ln in str(watermark).splitlines() if ln.strip()]
    if not lines:
        return ("", "")
    if len(lines) == 1:
        return (lines[0], "")
    return (lines[0], lines[1])


def _build_stat_block(
    *,
    title: str,
    value: str,
    since: str,
    theme: AliveCardTheme,
    font: str,
    width: int,
) -> VSplit:
    label_style = TextStyle(font=font, size=18, color=theme.text_sub)
    # value 可能很长（例如运行了上百天），这里略减字号并允许自动换行，
    # 让“统计块”高度自适应增长，避免溢出/省略号。
    value_style = TextStyle(font=font, size=36, color=theme.accent)
    since_style = TextStyle(font=font, size=16, color=theme.text_muted)

    block_items = [
        TextBox(title, style=label_style, wrap=False)
        .set_w(width)
        .set_content_align("l")
        .set_padding(0),
        Spacer(1, 6),
        TextBox(
            value,
            style=value_style,
            wrap=True,
            line_count=2,
            line_sep=2,
            overflow="shrink",
            use_real_line_count=True,
        )
        .set_w(width)
        .set_content_align("l")
        .set_padding(0),
        Spacer(1, 6),
        TextBox(f"Since {since}", style=since_style, wrap=False, overflow="shrink")
        .set_w(width)
        .set_content_align("l")
        .set_padding(0),
    ]

    block = (
        VSplit(items=block_items, sep=0, item_size_mode="fixed", item_align="l")
        .set_w(width)
        .set_padding((18, 16))
        .set_bg(RoundRectBg(fill=theme.block_bg, radius=20, stroke=theme.block_border, stroke_width=2))
    )
    return block


async def draw_alive_card(
    current_time: str,
    current_since: str,
    total_time: str,
    total_since: str,
    watermark: str,
    is_night: bool = False,
    width: int = DEFAULT_WIDTH,
) -> bytes:
    """绘制 Alive 状态卡片（基于 utils/draw/plot）。

    Returns:
        PNG bytes
    """
    theme = THEME_NIGHT if is_night else THEME_DAY
    font = _pick_font()

    brand_line, generated_line = _split_watermark(watermark)

    # ---- layout constants ----
    outer_margin = 26
    card_padding = 26
    inner_sep = 18
    block_sep = 18

    content_w = width - outer_margin * 2 - card_padding * 2

    # ---- header ----
    title_style = TextStyle(font=font, size=32, color=theme.text_main, use_shadow=False)
    subtitle_style = TextStyle(font=font, size=16, color=theme.text_sub)

    header_items = [
        TextBox("HakuBot Alive Status", style=title_style, wrap=False)
        .set_w(content_w)
        .set_content_align("l")
        .set_padding(0)
    ]
    if generated_line:
        header_items.append(
            TextBox(generated_line, style=subtitle_style, wrap=False, overflow="shrink")
            .set_w(content_w)
            .set_content_align("l")
            .set_padding(0)
        )

    header = (
        VSplit(items=header_items, sep=6, item_size_mode="fixed", item_align="l")
        .set_w(content_w)
        .set_padding(0)
    )

    # ---- stats blocks (two columns) ----
    block_w = (content_w - block_sep) // 2
    current_block = _build_stat_block(
        title="Current Session",
        value=str(current_time),
        since=str(current_since),
        theme=theme,
        font=font,
        width=block_w,
    )
    total_block = _build_stat_block(
        title="Total Runtime",
        value=str(total_time),
        since=str(total_since),
        theme=theme,
        font=font,
        width=block_w,
    )

    stats = (
        HSplit(items=[current_block, total_block], sep=block_sep, item_size_mode="fixed", item_align="t")
        .set_w(content_w)
        .set_padding(0)
    )

    # ---- footer watermark ----
    watermark_style = TextStyle(font=font, size=14, color=theme.text_muted)
    footer_items = []
    if brand_line:
        footer_items.append(
            TextBox(brand_line, style=watermark_style, wrap=False, overflow="shrink")
            .set_w(content_w)
            .set_content_align("r")
            .set_padding(0)
        )

    footer = (
        VSplit(items=footer_items, sep=0, item_size_mode="fixed", item_align="r")
        .set_w(content_w)
        .set_padding(0)
    )

    # ---- compose card ----
    card_items = [
        header,
        Spacer(1, inner_sep),
        stats,
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

    # ---- canvas background (gradient) ----
    canvas_bg = FillBg(
        LinearGradient(
            theme.canvas_g1,
            theme.canvas_g2,
            (0.0, 0.0),
            (1.0, 1.0),
            method="seperate",
        )
    )
    canvas = Canvas(w=width, h=None, bg=canvas_bg).set_items([card]).set_content_align("c")

    # background decorations (subtle blobs)
    def _decorations(widget, p):
        # left-top
        p.roundrect(
            (outer_margin - 10, outer_margin - 10),
            (180, 120),
            fill=(theme.accent[0], theme.accent[1], theme.accent[2], 28),
            radius=60,
            stroke=None,
            stroke_width=0,
        )
        # right-bottom
        p.roundrect(
            (p.w - 220, p.h - 160),
            (200, 140),
            fill=(theme.accent[0], theme.accent[1], theme.accent[2], 22),
            radius=70,
            stroke=None,
            stroke_width=0,
        )

    canvas.add_draw_func(_decorations)

    img = await canvas.get_img()

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
