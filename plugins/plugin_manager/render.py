from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Sequence

from ..utils.draw.plot import (
    Canvas,
    FillBg,
    Frame,
    HSplit,
    LinearGradient,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
    WHITE,
)
from ..utils.draw.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT, Painter, get_font, get_text_size


@dataclass(slots=True)
class PluginStatusRow:
    """A single display row: parent plugin or sub-feature."""
    name: str
    plugin_id: str
    enabled: bool
    is_child: bool = False


@dataclass(slots=True)
class PluginStatusGroup:
    """A card representing a parent plugin with optional child feature rows."""
    parent: PluginStatusRow
    children: list[PluginStatusRow]


def _estimate_group_height(g: PluginStatusGroup) -> int:
    """
    Rough height estimation for balancing columns.
    (We don't measure real widget heights; a coarse estimation is enough.)
    """
    base = 86  # parent row + paddings
    child = 52
    return base + (len(g.children) * child)


def _status_pill(enabled: bool) -> Frame:
    """Rounded rectangle status pill widget."""
    if enabled:
        bg = RoundRectBg(
            fill=(120, 205, 165, 235),
            radius=999,
            stroke=(90, 175, 140, 255),
            stroke_width=2,
        )
        text = "启用 / ON"
        color = WHITE
    else:
        bg = RoundRectBg(
            fill=(235, 155, 150, 235),
            radius=999,
            stroke=(210, 130, 125, 255),
            stroke_width=2,
        )
        text = "禁用 / OFF"
        color = WHITE

    pill = Frame().set_bg(bg).set_padding((12, 6)).set_content_align("c").set_margin(0)
    with pill:
        TextBox(
            text,
            style=TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=color),
            wrap=False,
        ).set_padding(0)
    return pill


def _plugin_row(row: PluginStatusRow, left_width: int) -> HSplit:
    """
    One row: (name + id) on the left, status pill on the right.
    """
    if row.enabled:
        text_color_main = (35, 55, 85, 255)
        text_color_sub = (85, 105, 135, 255)
    else:
        # disabled: lighter gray-ish text
        text_color_main = (140, 150, 165, 255)
        text_color_sub = (165, 175, 190, 255)

    name_style = TextStyle(
        font=DEFAULT_BOLD_FONT if not row.is_child else DEFAULT_FONT,
        size=24 if not row.is_child else 20,
        color=text_color_main,
    )
    id_style = TextStyle(
        font=DEFAULT_FONT,
        size=16,
        color=text_color_sub,
    )

    with HSplit(sep=10, item_align="l").set_content_align("l") as hs:
        # Left (text)
        with VSplit(sep=1, item_align="l").set_content_align("l"):
            indent = 14 if row.is_child else 0

            if indent:
                with HSplit(sep=6, item_align="l").set_content_align("l"):
                    Spacer(w=indent, h=1)
                    TextBox(row.name, style=name_style, wrap=True, use_real_line_count=True).set_w(
                        max(10, left_width - indent)
                    )
            else:
                TextBox(row.name, style=name_style, wrap=True, use_real_line_count=True).set_w(left_width)

            if indent:
                with HSplit(sep=6, item_align="l").set_content_align("l"):
                    Spacer(w=indent, h=1)
                    TextBox(f"{row.plugin_id}", style=id_style, wrap=False).set_w(max(10, left_width - indent))
            else:
                TextBox(f"{row.plugin_id}", style=id_style, wrap=False).set_w(left_width)

        # Right (pill)
        _status_pill(row.enabled)

    return hs


def _pick_column_count(groups: Sequence[PluginStatusGroup], canvas_w: int, header_h_est: int = 110) -> int:
    """
    Choose 1 or 2 columns to make output closer to square.
    We keep max 2 columns to avoid overly narrow cards on mobile clients.
    """
    if not groups:
        return 1

    total = sum(_estimate_group_height(g) for g in groups)
    h1 = header_h_est + total
    # Greedy split to estimate max column height for 2 columns
    col_h = [0, 0]
    for g in sorted(groups, key=_estimate_group_height, reverse=True):
        i = 0 if col_h[0] <= col_h[1] else 1
        col_h[i] += _estimate_group_height(g)
    h2 = header_h_est + max(col_h)

    # pick the one closer to square (height ~= width)
    diff1 = abs(h1 - canvas_w)
    diff2 = abs(h2 - canvas_w)

    if diff2 + 60 < diff1:
        return 2
    # also: if list is long, force 2 columns
    if len(groups) >= 16:
        return 2
    return 1


async def render_plugin_status_image(
    groups: Sequence[PluginStatusGroup],
    group_id: str,
    title: str = "插件开关状态",
    watermark_text: str = "",
    watermark_position: str = "bottom_right",
) -> bytes:
    """
    Render plugin switches as a single PNG.

    Returns:
        PNG bytes (not BytesIO).
    """
    # Softer light-blue theme
    bg = FillBg(
        LinearGradient(
            c1=(226, 238, 255, 255),
            c2=(244, 249, 255, 255),
            p1=(0, 0),
            p2=(1, 1),
        )
    )
    card_bg = RoundRectBg(
        fill=(242, 248, 255, 235),
        radius=14,
        stroke=(210, 226, 245, 255),
        stroke_width=2,
    )

    # Layout config
    canvas_w = 980
    outer_padding = 14
    card_padding = 12
    col_sep = 12
    card_sep = 8

    cols = _pick_column_count(groups, canvas_w=canvas_w)
    content_w = canvas_w - outer_padding * 2
    col_w = (content_w - col_sep * (cols - 1)) // cols

    # space for status pill + gap
    left_width = max(220, col_w - 160)

    canvas = Canvas(w=canvas_w, bg=bg).set_padding(outer_padding).set_margin(0)
    with canvas:
        with VSplit(sep=10, item_align="l").set_content_align("l"):
            # Header
            TextBox(
                title,
                style=TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(25, 50, 90, 255)),
                wrap=True,
                use_real_line_count=True,
            ).set_w(content_w)

            TextBox(
                f"Group: {group_id}",
                style=TextStyle(font=DEFAULT_FONT, size=18, color=(75, 95, 125, 255)),
                wrap=False,
            )

            Spacer(w=1, h=4)

            # Multi-column cards
            if cols == 1:
                with VSplit(sep=card_sep, item_align="l").set_content_align("l"):
                    for g in groups:
                        card = Frame().set_bg(card_bg).set_padding(card_padding).set_margin(0).set_content_align("l")
                        with card:
                            with VSplit(sep=8, item_align="l").set_content_align("l"):
                                _plugin_row(g.parent, left_width=left_width)
                                for child in g.children:
                                    _plugin_row(child, left_width=left_width)
            else:
                # 2 columns, waterfall balancing by estimated heights
                heights = [0, 0]
                cols_widgets: list[VSplit] = []

                with HSplit(
                    ratios=[1, 1],
                    sep=col_sep,
                    item_size_mode="expand",
                    item_align="l",
                ).set_w(content_w).set_content_align("l"):
                    for _ in range(2):
                        cols_widgets.append(VSplit(sep=card_sep, item_align="l").set_content_align("l"))

                    for g in groups:
                        idx = 0 if heights[0] <= heights[1] else 1
                        heights[idx] += _estimate_group_height(g)

                        with cols_widgets[idx]:
                            card = (
                                Frame()
                                .set_bg(card_bg)
                                .set_padding(card_padding)
                                .set_margin(0)
                                .set_content_align("l")
                            )
                            with card:
                                with VSplit(sep=8, item_align="l").set_content_align("l"):
                                    _plugin_row(g.parent, left_width=left_width)
                                    for child in g.children:
                                        _plugin_row(child, left_width=left_width)

    img = await canvas.get_img()

    # Watermark (optional)
    watermark_text = (watermark_text or "").strip()
    watermark_position = (watermark_position or "bottom_right").strip() or "bottom_right"
    if watermark_text:
        try:
            p = Painter(img)
            font_size = 16
            font = get_font(DEFAULT_FONT, font_size)

            # multi-line watermark support
            lines = [ln for ln in watermark_text.split("\n") if ln.strip()]
            if lines:
                line_height = font_size + 4
                padding = 12

                total_height = len(lines) * line_height
                start_y = img.height - total_height - padding

                for i, line in enumerate(lines):
                    w, _h = get_text_size(font, line)

                    if watermark_position == "bottom":
                        x = (img.width - w) // 2
                    else:
                        # bottom_right (default)
                        x = img.width - w - padding

                    y = start_y + i * line_height

                    # subtle shadow + body
                    p.text(line, (x + 1, y + 1), font, fill=(255, 255, 255, 120))
                    p.text(line, (x, y), font, fill=(90, 110, 140, 140))

                img = await p.get()
        except Exception:
            # watermark should never break the main image
            pass

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
