from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Optional

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

CST = timezone(timedelta(hours=8))


def format_number(value: Optional[int]) -> str:
    if value is None:
        return "--"
    return f"{int(value):,}"


def format_delta(current: Optional[int], prediction: Optional[int]) -> str:
    if current is None or prediction is None:
        return "--"
    delta = int(prediction) - int(current)
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{abs(delta):,}"


def format_time_text(dt: datetime) -> str:
    return dt.astimezone(CST).strftime("%m-%d %H:%M:%S")


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_event_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CST)


def build_event_meta(event_info: dict[str, Any], latest_data: dict[str, Any]) -> str:
    start_at = parse_event_ms(int(event_info["start_at"]))
    end_at = parse_event_ms(int(event_info["end_at"]))
    updated_at = format_time_text(parse_iso_datetime(latest_data["updated_at"]))
    return (
        f'Event {event_info["event_id"]}\n'
        f"活动时间：{start_at.strftime('%m-%d %H:%M')} ~ {end_at.strftime('%m-%d %H:%M')}\n"
        f"数据更新时间：{updated_at}"
    )


def build_footer(latest_data: dict[str, Any]) -> str:
    return f'状态：{latest_data.get("status", "unknown")}｜来源：Moesekai | pjsk.moe'


async def render_prediction_card(event_info: dict[str, Any], latest_data: dict[str, Any]) -> bytes:
    colors = {
        "canvas_bg": (228, 245, 255, 255),
        "card_bg": (243, 251, 255, 255),
        "card_border": (170, 210, 235, 255),
        "header_bg": (214, 237, 252, 255),
        "table_header_bg": (207, 229, 244, 255),
        "row_bg": (236, 248, 255, 255),
        "text_main": (25, 55, 75, 255),
        "text_sub": (80, 120, 140, 255),
        "text_muted": (120, 150, 165, 255),
        "accent": (49, 130, 206, 255),
    }

    title_style = TextStyle(font="SourceHanSansCN-Heavy", size=40, color=colors["text_main"])
    subtitle_style = TextStyle(font="SourceHanSansCN-Bold", size=24, color=colors["accent"])
    meta_style = TextStyle(font="SourceHanSansCN-Regular", size=18, color=colors["text_sub"])
    table_head_style = TextStyle(font="SourceHanSansCN-Bold", size=18, color=colors["text_main"])
    table_cell_style = TextStyle(font="SourceHanSansCN-Regular", size=19, color=colors["text_main"])
    footer_style = TextStyle(font="SourceHanSansCN-Regular", size=16, color=colors["text_sub"])
    watermark_style = TextStyle(font="SourceHanSansCN-Regular", size=14, color=colors["text_muted"])

    width = 920
    outer_margin = 26
    card_padding_x = 24
    card_padding_y = 24
    content_w = width - outer_margin * 2 - card_padding_x * 2

    items = latest_data.get("items") or []

    sections: list = []
    sections.append(
        TextBox(event_info["name"], style=title_style, wrap=True, use_real_line_count=True)
        .set_w(content_w)
        .set_padding(0)
    )
    sections.append(
        TextBox(build_event_meta(event_info, latest_data), style=meta_style, wrap=True, use_real_line_count=True)
        .set_w(content_w)
        .set_padding(0)
    )
    sections.append(Spacer(1, 20))

    col_ratios = [1.4, 2.5, 2.5, 2.0]
    inner_sep = 12

    def make_table_row(
        rank_text: str,
        score_text: str,
        prediction_text: str,
        delta_text: str,
        *,
        is_header: bool = False,
    ):
        style = table_head_style if is_header else table_cell_style
        bg = colors["table_header_bg"] if is_header else colors["row_bg"]

        cols = [
            TextBox(rank_text, style=style, wrap=False, overflow="shrink").set_padding(0).set_content_align("c"),
            TextBox(score_text, style=style, wrap=False, overflow="shrink").set_padding(0).set_content_align("r"),
            TextBox(prediction_text, style=style, wrap=False, overflow="shrink").set_padding(0).set_content_align("r"),
            TextBox(delta_text, style=style, wrap=False, overflow="shrink").set_padding(0).set_content_align("r"),
        ]

        return (
            HSplit(items=cols, sep=inner_sep, item_size_mode="expand", ratios=col_ratios)
            .set_w(content_w)
            .set_padding((18, 12 if is_header else 10))
            .set_bg(RoundRectBg(fill=bg, radius=16))
        )

    row_widgets = [
        make_table_row("档位", "当前线", "预测线", "差值", is_header=True),
    ]

    for item in items:
        rank = item.get("rank")
        score = item.get("score")
        prediction = item.get("prediction")
        row_widgets.append(
            make_table_row(
                f"T{rank}" if rank is not None else "--",
                format_number(score),
                format_number(prediction),
                format_delta(score, prediction),
            )
        )

    if len(row_widgets) == 1:
        row_widgets.append(make_table_row("--", "--", "--", "--"))

    sections.append(
        VSplit(items=row_widgets, sep=10, item_size_mode="fixed", item_align="c")
        .set_w(content_w)
        .set_padding(0)
    )
    sections.append(Spacer(1, 18))
    sections.append(
        TextBox(build_footer(latest_data), style=footer_style, wrap=True, use_real_line_count=True)
        .set_w(content_w)
        .set_padding(0)
    )
    sections.append(Spacer(1, 8))
    sections.append(
        TextBox("Generated by HakuBot", style=watermark_style, wrap=False)
        .set_w(content_w)
        .set_content_align("r")
        .set_padding(0)
    )

    card = (
        VSplit(items=sections, sep=14, item_size_mode="fixed", item_align="c")
        .set_w(width - outer_margin * 2)
        .set_padding((card_padding_x, card_padding_y))
        .set_margin(outer_margin)
        .set_bg(RoundRectBg(fill=colors["card_bg"], radius=26, stroke=colors["card_border"], stroke_width=2))
    )

    canvas = Canvas(w=width, h=None, bg=FillBg(colors["canvas_bg"]))
    canvas.set_items([card]).set_content_align("c")
    img = await canvas.get_img()

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
