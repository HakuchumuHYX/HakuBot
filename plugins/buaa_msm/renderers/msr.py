# plugins/buaa_msm/renderers/msr.py
"""
MSR 渲染器（纯渲染）：输出 PNG bytes。

说明：
- 这里不包含 NoneBot 的发送/提示逻辑；只负责把结构化数据渲染成图片 bytes。
- 由 renderers 层集中承载统计图/位置图绘制逻辑，便于维护与复用。
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .. import analysis
from ..config import MAP_ORDER, SCENE_KEY_TO_NAME, plugin_config
from ..resources.catalog import (
    ITEM_TEXTURES,
    RARE_ITEM,
    SCENES,
    SUPER_RARE_ITEM,
    get_font,
    get_icon,
    resource_dir,
)
from ..services.masterdata_lite import masterdata_lite
from ..services.rip_asset_lite import rip_asset_lite

ColorRGB = Tuple[int, int, int]
ColorRGBA = Tuple[int, int, int, int]


# ============================ small utils ============================


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _rarity_level(category: str, item_id: int) -> int:
    """2: super rare, 1: rare, 0: normal"""
    if category in SUPER_RARE_ITEM and item_id in SUPER_RARE_ITEM[category]:
        return 2
    if category in RARE_ITEM and item_id in RARE_ITEM[category]:
        return 1
    return 0


def _get_texture_path(category: str, item_id: int) -> Optional[str]:
    # 1) 唱片保持现有逻辑：优先走封面缓存，失败时用默认 surplus 图标
    if category == "mysekai_music_record":
        return str(resource_dir / "icon" / "Texture2D" / "item_surplus_music_record.png")

    # 2) 动态 icon（已在 orchestration 阶段异步预取到本地）
    dyn = rip_asset_lite.get_cached_icon_path(category, item_id)
    if dyn:
        return dyn

    # 3) 静态 fallback
    return ITEM_TEXTURES.get(category, {}).get(str(item_id))


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _gradient_bg(size: Tuple[int, int]) -> Image.Image:
    """
    柔和背景：浅蓝系竖向渐变 + 轻微纹理
    - 整体更偏中性浅蓝（降低白度）
    - 纹理不再用 add（会增亮），改为 multiply（轻微压暗且更柔和）
    """
    w, h = size
    top = (232, 242, 252)
    mid = (220, 235, 250)
    bot = (210, 228, 248)

    img = Image.new("RGB", (w, h), top)
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        if t < 0.5:
            tt = t / 0.5
            c = (_lerp(top[0], mid[0], tt), _lerp(top[1], mid[1], tt), _lerp(top[2], mid[2], tt))
        else:
            tt = (t - 0.5) / 0.5
            c = (_lerp(mid[0], bot[0], tt), _lerp(mid[1], bot[1], tt), _lerp(mid[2], bot[2], tt))
        for x in range(w):
            px[x, y] = c

    noise = Image.effect_noise((w, h), 10).convert("L")
    noise = noise.point(lambda p: 235 + int(p * 20 / 255))
    img = ImageChops.multiply(img, Image.merge("RGB", (noise, noise, noise)))
    return img


def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    return mask


def _paste_with_shadow(
    dst: Image.Image,
    src: Image.Image,
    pos: Tuple[int, int],
    shadow: bool = True,
    shadow_radius: int = 10,
    shadow_offset: Tuple[int, int] = (0, 4),
):
    if not shadow:
        dst.paste(src, pos, src if src.mode == "RGBA" else None)
        return

    if src.mode != "RGBA":
        src = src.convert("RGBA")

    w, h = src.size
    x, y = pos
    sx, sy = shadow_offset
    alpha = src.split()[-1]
    shadow_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow_img.putalpha(alpha)
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=shadow_radius))
    shadow_img = ImageChops.multiply(shadow_img, Image.new("RGBA", (w, h), (0, 0, 0, 90)))
    dst.paste(shadow_img, (x + sx, y + sy), shadow_img)
    dst.paste(src, (x, y), src)


def _draw_card(
    dst: Image.Image,
    rect: Tuple[int, int, int, int],
    radius: int = 18,
    fill: ColorRGBA = (245, 250, 255, 205),
):
    """
    卡片：半透明白底 + 圆角（去掉阴影，避免“脏/糊”的观感）
    """
    x1, y1, x2, y2 = rect
    w, h = x2 - x1, y2 - y1

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(card, "RGBA")
    d.rounded_rectangle(
        (0, 0, w - 1, h - 1),
        radius=radius,
        fill=fill,
        outline=(230, 240, 250, 170),
        width=2,
    )

    if dst.mode != "RGBA":
        dst_rgba = dst.convert("RGBA")
        dst_rgba.paste(card, (x1, y1), card)
        dst.paste(dst_rgba.convert("RGB"), (0, 0))
        return

    dst.paste(card, (x1, y1), card)


def _draw_text_with_stroke(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: ColorRGBA,
    stroke_fill: ColorRGBA = (255, 255, 255, 220),
    stroke: int = 2,
):
    x, y = xy
    for dx in (-stroke, 0, stroke):
        for dy in (-stroke, 0, stroke):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y), text, font=font, fill=fill)


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    try:
        return font.getbbox(text)[2]
    except Exception:
        try:
            return font.getsize(text)[0]
        except Exception:
            return len(text) * 10


def _ellipsize(font: ImageFont.FreeTypeFont, text: str, max_width: int) -> str:
    """按像素宽度裁剪字符串并加省略号，避免右侧唱片列表越界。"""
    if _text_width(font, text) <= max_width:
        return text
    ell = "…"
    for i in range(max(len(text) - 1, 1), 0, -1):
        cand = text[:i] + ell
        if _text_width(font, cand) <= max_width:
            return cand
    return ell


def _wrap_text(font: ImageFont.FreeTypeFont, text: str, max_width: int) -> List[str]:
    """
    简单按像素宽度换行：
    - 兼容原文本中的 \\n
    - 优先按空格分词；若无空格则按字符拆分
    """
    lines: List[str] = []

    paragraphs = (text or "").split("\n")
    for para in paragraphs:
        para = para.rstrip()
        if not para:
            lines.append("")
            continue

        if _text_width(font, para) <= max_width:
            lines.append(para)
            continue

        has_space = " " in para
        tokens = para.split(" ") if has_space else list(para)

        cur = ""
        for tok in tokens:
            cand = tok if not cur else (f"{cur} {tok}" if has_space else f"{cur}{tok}")
            if _text_width(font, cand) <= max_width:
                cur = cand
                continue

            if cur:
                lines.append(cur)
                cur = tok
            else:
                # 单个 token 也超过 max_width：强行截断
                tmp = tok
                while tmp:
                    # 从长到短找能放下的片段
                    cut = tmp
                    while cut and _text_width(font, cut) > max_width:
                        cut = cut[:-1]
                    if not cut:
                        break
                    lines.append(cut)
                    tmp = tmp[len(cut) :]

        if cur:
            lines.append(cur)

    return lines


# ============================ tile (icon block) ============================


TILE_FILL: ColorRGBA = (195, 225, 245, 220)
TILE_STROKE: ColorRGBA = (232, 244, 255, 170)
TILE_RADIUS = 8

RARE_STROKE: ColorRGBA = (80, 120, 255, 230)
SUPER_RARE_STROKE: ColorRGBA = (255, 90, 90, 235)

# ============== map-point markers (for summary thumbnails) ==============

# legacy 中的点位颜色映射（按 fixtureId）
# 这里复制一份，避免从 legacy renderer import 导致潜在循环依赖
_FIXTURE_COLORS = {
    112: "#f9f9f9",
    1001: "#da6d42",
    1002: "#da6d42",
    1003: "#da6d42",
    1004: "#da6d42",
    2001: "#878685",
    2002: "#d5750a",
    2003: "#d5d5d5",
    2004: "#a7c7cb",
    2005: "#9933cc",
    3001: "#c95a49",
    4001: "#f8729a",
    4002: "#f8729a",
    4003: "#f8729a",
    4004: "#f8729a",
    4005: "#f8729a",
    4006: "#f8729a",
    4007: "#f8729a",
    4008: "#f8729a",
    4009: "#f8729a",
    4010: "#f8729a",
    4011: "#f8729a",
    4012: "#f8729a",
    4013: "#f8729a",
    4014: "#f8729a",
    4015: "#f8729a",
    4016: "#f8729a",
    4017: "#f8729a",
    4018: "#f8729a",
    4019: "#f8729a",
    4020: "#f8729a",
    5001: "#f6f5f2",
    5002: "#f6f5f2",
    5003: "#f6f5f2",
    5004: "#f6f5f2",
    5101: "#f6f5f2",
    5102: "#f6f5f2",
    5103: "#f6f5f2",
    5104: "#f6f5f2",
    6001: "#6f4e37",
    7001: "#a5d5ff",
}


def _fixture_color_rgb(fixture_id: int) -> Tuple[int, int, int]:
    c = _FIXTURE_COLORS.get(fixture_id, "#000000")
    try:
        return ImageColor.getrgb(c)
    except Exception:
        return (0, 0, 0)


def _contains_rare_item(reward: Dict[str, Any]) -> bool:
    """是否包含 rare/super rare item（用于缩略图点位描边）"""
    try:
        for category, items in (reward or {}).items():
            if category in SUPER_RARE_ITEM:
                for item_id_str in (items or {}).keys():
                    if _safe_int(item_id_str, -1) in SUPER_RARE_ITEM[category]:
                        return True
            if category in RARE_ITEM:
                for item_id_str in (items or {}).keys():
                    if _safe_int(item_id_str, -1) in RARE_ITEM[category]:
                        return True
    except Exception:
        return False
    return False


def _resolve_harvest_fixture_marker(fixture_id: int, grid_px: float) -> Tuple[Optional[str], int, Tuple[int, int]]:
    """
    资源点本体材质图：
    1) 动态缓存（haruki 资产，已由 orchestration 预取）
    2) 静态目录兜底（若项目内存在）
    3) 返回 None 让上层回退圆点
    """
    if not plugin_config.mysekai_dynamic_icon_enabled:
        return None, 0, (0, 0)

    meta = masterdata_lite.get_harvest_fixture_meta(int(fixture_id))
    if not meta:
        return None, 0, (0, 0)

    icon_path = rip_asset_lite.get_cached_harvest_fixture_icon_path(int(fixture_id))
    if not icon_path:
        rarity_raw = str(meta.get("rarity", "")).strip()
        asset_name = str(meta.get("assetbundleName", "")).strip()

        rarity_dirs: List[str] = []
        if rarity_raw:
            rarity_dirs.append(rarity_raw)
            if rarity_raw.isdigit():
                rarity_dirs.append(f"rarity_{rarity_raw}")
            elif rarity_raw.startswith("rarity_"):
                plain = rarity_raw.replace("rarity_", "", 1)
                if plain.isdigit():
                    rarity_dirs.append(plain)

        for rarity_dir in rarity_dirs:
            candidates = [
                plugin_config.data_dir / "harvest_fixture_icon" / rarity_dir / f"{asset_name}.png",
                resource_dir / "mysekai" / "harvest_fixture_icon" / rarity_dir / f"{asset_name}.png",
            ]
            hit = next((p for p in candidates if p.exists()), None)
            if hit:
                icon_path = str(hit)
                break

    if not icon_path:
        return None, 0, (0, 0)

    # 位置图里材质图略大于圆点，且需要上移对齐采集点
    icon_size = max(30, int(grid_px * 5.0))
    offset = (int(-icon_size * 0.5), int(-icon_size * 0.78))
    return icon_path, icon_size, offset


def _make_tile_base(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=TILE_RADIUS,
        fill=TILE_FILL,
        outline=TILE_STROKE,
        width=2,
    )
    return img


_tile_base_cache: Dict[int, Image.Image] = {}
_tile_base_lock = threading.Lock()


def _get_tile_base(size: int) -> Image.Image:
    # 统计图与位置图并行渲染时可能在不同线程里同时触发该缓存写入
    # 因此加锁避免竞态
    with _tile_base_lock:
        if size not in _tile_base_cache:
            _tile_base_cache[size] = _make_tile_base(size)
        return _tile_base_cache[size].copy()


def _tile_qty_color(qty: int) -> ColorRGBA:
    if qty == 2:
        return (200, 20, 0, 255)
    if qty > 2:
        return (180, 20, 200, 255)
    return (60, 60, 60, 255)


def _paste_icon_on_tile(tile: Image.Image, icon: Image.Image, padding: int = 4) -> Image.Image:
    tile = tile.copy()
    if icon.mode != "RGBA":
        icon = icon.convert("RGBA")
    tw, th = tile.size
    target = tw - padding * 2
    icon2 = icon.resize((target, target), Image.Resampling.LANCZOS)
    tile.paste(icon2, (padding, padding), icon2)
    return tile


def _apply_rarity_border(tile: Image.Image, rarity: int, *, size: int, width: int = 4) -> Image.Image:
    """按稀有度描边（替代光晕；线条更粗更清晰）"""
    if rarity not in (1, 2):
        return tile
    td = ImageDraw.Draw(tile, "RGBA")
    stroke = SUPER_RARE_STROKE if rarity == 2 else RARE_STROKE
    td.rounded_rectangle(
        (2, 2, size - 3, size - 3),
        radius=TILE_RADIUS,
        outline=stroke,
        width=width,
    )
    return tile


# ============================ Summary Image ============================


def generate_msr_summary_image_bytes(
    *,
    analysis_data: analysis.AggregatedData,
    visiting_characters: Dict[str, Dict[str, Any]],
    owned_music_records: Set[str],
    highlight_characters: Set[str],
    jacket_cache: Optional[Dict[str, Image.Image]] = None,
) -> bytes:
    """
    统计图：
    - 背景渐变
    - 来访角色卡（含今日唱片列表）
    - 4 张地图卡：左侧缩略图 + 右侧“图标方块 + 大数字”网格
    """
    padding = 26
    w = 1000

    font_title = get_font(30)
    font_h2 = get_font(22)
    font_num = get_font(28)
    font_text = get_font(16)
    font_small = get_font(14)

    # 汇总唱片掉落
    record_qty: Dict[str, int] = {}
    for map_name, summary in analysis_data.items():
        for (category, item_id_str), qty in (summary or {}).items():
            if category != "mysekai_music_record":
                continue
            key = str(item_id_str)
            record_qty[key] = record_qty.get(key, 0) + _safe_int(qty, 0)

    max_record_lines = 10
    record_items: List[Tuple[str, int]] = sorted(record_qty.items(), key=lambda kv: (-kv[1], kv[0]))[:max_record_lines]

    # jacket_cache fallback
    _jc = jacket_cache or {}

    # 预计算每张地图卡高度
    map_cards = [m for m in MAP_ORDER if analysis_data.get(m)]
    items_by_map: Dict[str, List[Tuple[str, int, int, int]]] = {}
    card_h_by_map: Dict[str, int] = {}

    col_count = 5
    cell_h = 54
    min_card_h = 190

    for map_name in map_cards:
        summary = analysis_data.get(map_name) or {}
        items: List[Tuple[str, int, int, int]] = []
        for (category, item_id_str), qty in summary.items():
            item_id = _safe_int(item_id_str, 0)
            rarity = _rarity_level(category, item_id)
            items.append((category, item_id, _safe_int(qty, 0), rarity))

        items.sort(key=lambda t: (-t[3], -t[2], t[1]))
        items_by_map[map_name] = items

        rows = (len(items) + col_count - 1) // col_count
        card_h_by_map[map_name] = max(min_card_h, 44 + rows * cell_h + 18)

    estimated_y = 100
    char_lines = min(len(visiting_characters), 6) if visiting_characters else 0
    record_lines = len(record_items)
    record_row_h = 90  # 封面 80px + 间距
    char_row_h = 22
    if visiting_characters or record_items:
        left_col_h = char_lines * char_row_h
        right_col_h = record_lines * record_row_h
        visiting_card_h = 44 + max(left_col_h, right_col_h) + 16
        estimated_y += visiting_card_h + 18

    for map_name in map_cards:
        estimated_y += card_h_by_map.get(map_name, min_card_h) + 18

    # watermark（右下角，浅灰色 + 自动换行 + 自适应边距；同时预留底部空间避免盖住内容）
    watermark = (
        "Designed by MiddleRed and NeuraXmy\n"
        "Generated by HakuBot\n"
        "DO NOT REPOST THIS IMAGE ON ANY SOCIAL MEDIA PLATFORM"
    )
    margin_x = 18
    margin_y = 14
    max_wm_width = min(560, w - margin_x * 2)
    wm_lines = _wrap_text(font_small, watermark, max_wm_width)

    # 行高估算 + 行间距
    line_gap = 4
    try:
        bbox = font_small.getbbox("Ag")
        line_h = (bbox[3] - bbox[1]) + line_gap
    except Exception:
        line_h = font_small.getsize("Ag")[1] + line_gap

    block_h = line_h * len(wm_lines)

    # bottom_reserved：不仅要贴边，还要保证水印块不会压到内容区
    bottom_reserved = max(padding, margin_y + block_h + 10)
    h = estimated_y + bottom_reserved

    bg = _gradient_bg((w, h)).convert("RGBA")
    draw = ImageDraw.Draw(bg)

    draw.text((padding, 18), "MySekai 资源采集报告", fill=(60, 60, 60), font=font_title)

    y = 100

    # 来访角色卡 + 今日唱片
    if visiting_characters or record_items:
        left_col_h = char_lines * char_row_h
        right_col_h = record_lines * record_row_h
        card_h = 44 + max(left_col_h, right_col_h) + 16
        _draw_card(bg, (padding, y, w - padding, y + card_h))

        d = ImageDraw.Draw(bg)
        card_w = (w - padding) - padding
        left_x = padding + 18
        right_x = padding + int(card_w * 0.56)
        right_max_w = (w - padding - 18) - right_x

        d.text((left_x, y + 12), "今日来访角色", fill=(30, 120, 60), font=font_h2)
        y2 = y + 44
        if visiting_characters:
            for group_id, info in sorted(
                visiting_characters.items(), key=lambda kv: kv[1].get("count", 0), reverse=True
            )[:6]:
                name = str(info.get("name", group_id))
                count = _safe_int(info.get("count", 0))
                suffix = "（今日已来访）" if group_id in highlight_characters else ""
                d.text((left_x + 4, y2), f"• {name}：{count}次 {suffix}".strip(), fill=(70, 70, 70), font=font_text)
                y2 += 22

        d.text((right_x, y + 12), "今日唱片", fill=(60, 90, 170), font=font_h2)
        y3 = y + 44
        jacket_thumb_size = 80
        for record_id, qty in record_items:
            # 尝试绘制封面缩略图
            jimg = _jc.get(str(record_id))
            text_offset_x = 0
            if jimg is not None:
                try:
                    jthumb = jimg.resize((jacket_thumb_size, jacket_thumb_size), Image.Resampling.LANCZOS).convert("RGBA")
                    # 圆角遮罩
                    jmask = _rounded_rect_mask((jacket_thumb_size, jacket_thumb_size), 6)
                    jthumb.putalpha(jmask)
                    bg.paste(jthumb, (right_x, y3), jthumb)
                    d = ImageDraw.Draw(bg)
                    text_offset_x = jacket_thumb_size + 8
                except Exception:
                    pass

            # 文字垂直居中于封面
            text_y = y3 + (jacket_thumb_size - 16) // 2 if text_offset_x > 0 else y3 + 2
            name = analysis.get_resource_name("mysekai_music_record", str(record_id))
            line = f"• {name}：{qty}个"
            line = _ellipsize(font_text, line, max(10, right_max_w - 60 - text_offset_x))
            d.text((right_x + text_offset_x, text_y), line, fill=(70, 70, 70), font=font_text)

            if str(record_id) in owned_music_records:
                suffix = "（已获得）"
                sw = _text_width(font_text, line)
                d.text((right_x + text_offset_x + sw + 6, text_y + 1), suffix, fill=(140, 140, 140), font=font_small)

            y3 += record_row_h

        y += card_h + 18

    # 地图缩略图缓存
    scene_thumb_map: Dict[str, Image.Image] = {}
    for scene_key, scene in SCENES.items():
        name = SCENE_KEY_TO_NAME.get(scene_key)
        if not name:
            continue
        try:
            im = Image.open(scene["imagePath"]).convert("RGB")
        except Exception:
            im = Image.new("RGB", (400, 300), (200, 200, 200))
        scene_thumb_map[name] = im

    card_w = w - padding * 2
    for map_name in map_cards:
        card_h = card_h_by_map.get(map_name, 190)
        _draw_card(bg, (padding, y, padding + card_w, y + card_h))

        d = ImageDraw.Draw(bg)
        title = analysis.get_translated_map_name(map_name)
        d.text((padding + 18, y + 10), title, fill=(120, 80, 150), font=font_h2)

        thumb = scene_thumb_map.get(map_name)
        thumb_box = (padding + 18, y + 52, padding + 18 + 220, y + 52 + 120)
        if thumb:
            thumb2 = thumb.copy().resize((220, 120), Image.Resampling.LANCZOS)
            mask = _rounded_rect_mask((220, 120), 16)
            thumb_rgba = thumb2.convert("RGBA")
            thumb_rgba.putalpha(mask)
            out = bg.convert("RGBA")
            _paste_with_shadow(out, thumb_rgba, (thumb_box[0], thumb_box[1]), shadow=False)
            bg.paste(out.convert("RGB"), (0, 0))
        else:
            d.rectangle(thumb_box, outline=(200, 200, 200), width=2)

        # 右侧网格
        items = items_by_map.get(map_name, [])
        grid_x = padding + 18 + 240
        grid_y = y + 44
        grid_w = padding + card_w - 18 - grid_x
        col_count = 5
        cell_w = int(grid_w / col_count)
        cell_h = 54

        tile_sz = 40
        tile_pad = 7

        for idx, (category, item_id, qty, rarity) in enumerate(items):
            r = idx // col_count
            c = idx % col_count
            cx = grid_x + c * cell_w
            cy = grid_y + r * cell_h

            # 优先使用 jacket_cache 中的封面图
            icon = None
            if category == "mysekai_music_record" and str(item_id) in _jc:
                try:
                    icon = _jc[str(item_id)].resize((tile_sz - 8, tile_sz - 8), Image.Resampling.LANCZOS)
                except Exception:
                    icon = None

            if icon is None:
                tex = _get_texture_path(category, item_id)
                if tex:
                    try:
                        icon = get_icon(tex, (tile_sz - 8, tile_sz - 8))
                    except Exception:
                        icon = None

            tile = _get_tile_base(tile_sz)
            if icon is not None:
                tile = _paste_icon_on_tile(tile, icon, padding=4)
            tile = _apply_rarity_border(tile, rarity, size=tile_sz, width=4)

            _paste_with_shadow(bg, tile, (cx + tile_pad, cy + 8), shadow=False)

            num_color: ColorRGBA = (70, 70, 70, 255)
            if rarity == 2:
                num_color = (200, 30, 30, 255)
            elif rarity == 1:
                num_color = (60, 80, 220, 255)

            num_x = cx + tile_pad + tile_sz + 8
            num_y = cy + 10
            d.text((num_x, num_y), str(qty), fill=num_color, font=font_num)

            if category == "mysekai_music_record" and str(item_id) in owned_music_records:
                d.text((num_x, num_y + 30), "已获得", fill=(140, 140, 140), font=font_small)

        y += card_h + 18

    # watermark（右下角；不覆盖内容区：前面已为其预留 bottom_reserved）
    y0 = max(0, h - margin_y - block_h)

    d = ImageDraw.Draw(bg)
    for i, line in enumerate(wm_lines):
        lw = _text_width(font_small, line)
        x = max(0, w - margin_x - lw)
        y_line = y0 + i * line_h
        d.text((x, y_line), line, fill=(155, 155, 155), font=font_small)

    bg_rgb = ImageEnhance.Brightness(bg.convert("RGB")).enhance(0.94)

    out = io.BytesIO()
    bg_rgb.save(out, format="PNG")
    return out.getvalue()


# ============================ Map Image ============================


@dataclass
class _DropDrawCall:
    x: int
    y: int
    size: int
    tile: Image.Image
    qty: int
    rarity: int
    small: bool
    order: int


def _compute_small_icon_flags(drops: List[Tuple[str, int, int]]) -> Dict[Tuple[str, int], bool]:
    """
    简化版规则：
    - 如果同点位存在 mysekai_material，则非 mysekai_material 全部 small
    - 否则都不 small
    """
    has_material = any(c == "mysekai_material" for c, _, _ in drops)
    flags: Dict[Tuple[str, int], bool] = {}
    for c, item_id, _ in drops:
        flags[(c, item_id)] = bool(has_material and c != "mysekai_material")
    return flags


def stitch_images_grid_memory(images: List[Image.Image]) -> Image.Image:
    """内存版拼接：带蓝色边框/分隔线"""
    if not images:
        return Image.new("RGB", (1, 1), "white")
    if len(images) == 1:
        return images[0]

    max_w = max(im.width for im in images)
    max_h = max(im.height for im in images)

    border = 10
    line = 10
    blue = (40, 90, 170)

    if len(images) >= 4:
        imgs = images[:4]
        dst = Image.new("RGB", (max_w * 2 + border * 2 + line, max_h * 2 + border * 2 + line), blue)

        p00 = (border, border)
        p01 = (border + max_w + line, border)
        p10 = (border, border + max_h + line)
        p11 = (border + max_w + line, border + max_h + line)

        dst.paste(imgs[0], p00)
        dst.paste(imgs[1], p01)
        dst.paste(imgs[2], p10)
        dst.paste(imgs[3], p11)

        return dst

    total_h = sum(im.height for im in images) + border * 2 + line * (len(images) - 1)
    dst = Image.new("RGB", (max_w + border * 2, total_h), blue)
    cur_y = border
    for im in images:
        dst.paste(im, (border + (max_w - im.width) // 2, cur_y))
        cur_y += im.height + line
    return dst


def generate_msr_map_image_bytes(*, parsed_maps: Dict[str, List]) -> bytes:
    """
    位置图：
    - 不画黑点/列表框，直接画“青色方块掉落”
    - 角标数量 + 稀有描边（无光晕）
    - 出生点粉点（0,0）
    """
    scene_imgs: List[Image.Image] = []
    qty_font = get_font(14)

    for scene_key in SCENES.keys():
        scene_name = SCENE_KEY_TO_NAME.get(scene_key)
        if not scene_name:
            continue
        map_data = parsed_maps.get(scene_name)
        if map_data is None:
            continue

        scene = SCENES[scene_key]

        try:
            base_img = Image.open(scene["imagePath"]).convert("RGBA")
        except Exception:
            base_img = Image.new("RGBA", (800, 600), (220, 220, 220, 255))

        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay, "RGBA")

        grid_px = scene["physicalWidth"]
        origin_x = base_img.width / 2 + scene["offsetX"]
        origin_y = base_img.height / 2 + scene["offsetY"]
        reverse_xy = scene["reverseXY"]
        x_dir = scene["xDirection"]
        y_dir = scene["yDirection"]

        def to_xy(loc: Tuple[int, int]) -> Tuple[float, float]:
            x, y = (loc[1], loc[0]) if reverse_xy else (loc[0], loc[1])
            px_x = origin_x + x * grid_px if x_dir == "x+" else origin_x - x * grid_px
            px_y = origin_y + y * grid_px if y_dir == "y+" else origin_y - y * grid_px
            return px_x, px_y

        calls: List[_DropDrawCall] = []

        # 出生点粉点
        sx, sy = to_xy((0, 0))
        d.ellipse(
            (sx - 7, sy - 7, sx + 7, sy + 7),
            fill=(255, 80, 170, 255),
            outline=(255, 255, 255, 220),
            width=2,
        )

        for point in map_data:
            loc = point["location"]
            reward = point.get("reward", {})
            px_x, px_y = to_xy(loc)

            # 点位标记：
            # - 优先绘制 harvest fixture 本体材质图
            # - 失败时回退默认圆点标记
            # - 即使该点位的 tile 掉落被过滤（例如只有木头/石头），也保留点位标记
            if reward:
                fixture_id = _safe_int(point.get("fixtureId"), 0)
                marker_drawn = False

                if fixture_id > 0:
                    marker_path, marker_size, marker_offset = _resolve_harvest_fixture_marker(fixture_id, grid_px)
                    if marker_path and marker_size > 0:
                        try:
                            marker = get_icon(marker_path, (marker_size, marker_size))
                            mx = int(px_x + marker_offset[0])
                            my = int(px_y + marker_offset[1])
                            _paste_with_shadow(overlay, marker, (mx, my), shadow=False)
                            marker_drawn = True
                        except Exception:
                            marker_drawn = False

                if not marker_drawn:
                    fill_rgb = _fixture_color_rgb(fixture_id)
                    outline_rgb = (255, 0, 0) if _contains_rare_item(reward) else (0, 0, 0)
                    r_dot = 6
                    d.ellipse(
                        (px_x - r_dot, px_y - r_dot, px_x + r_dot, px_y + r_dot),
                        fill=(*fill_rgb, 210),
                        outline=(*outline_rgb, 255),
                        width=2,
                    )

            all_drops: List[Tuple[str, int, int]] = []
            for category, items in reward.items():
                for item_id_raw, qty in items.items():
                    item_id = _safe_int(item_id_raw, 0)
                    all_drops.append((category, item_id, _safe_int(qty, 0)))

            # 位置图过滤：普通木头/石头不绘制
            # translations.json: mysekai_material -> 1.木头 / 6.石头
            # 注意：是“按 item 过滤”，同一个点位里如果还有其他掉落，会继续绘制其他掉落
            drops: List[Tuple[str, int, int]] = [
                (category, item_id, qty)
                for (category, item_id, qty) in all_drops
                if not (category == "mysekai_material" and item_id in (1, 6))
            ]

            # 如果过滤后没有任何 tile 掉落，则仅保留上面的点位圆点
            if not drops:
                continue

            small_flags = _compute_small_icon_flags(drops)
            large = [(c, item_id, qty) for (c, item_id, qty) in drops if not small_flags.get((c, item_id), False)]
            small = [(c, item_id, qty) for (c, item_id, qty) in drops if small_flags.get((c, item_id), False)]

            large_sz = 34
            small_sz = 26

            total_w = len(large) * large_sz + max(0, len(large) - 1) * 4
            start_x = int(px_x - total_w / 2)
            base_y = int(px_y - (large_sz + 16))

            small_x = int(px_x + large_sz // 2 + 6)
            small_y = base_y

            def add_call(cx: int, cy: int, category: str, item_id: int, qty: int, *, small_icon: bool):
                tex = _get_texture_path(category, item_id)
                if not tex:
                    return

                sz = small_sz if small_icon else large_sz
                try:
                    icon = get_icon(tex, (sz - 8, sz - 8))
                except Exception:
                    return

                tile = _get_tile_base(sz)
                tile = _paste_icon_on_tile(tile, icon, padding=3)

                rarity = _rarity_level(category, item_id)
                tile = _apply_rarity_border(tile, rarity, size=sz, width=4)

                base_order = int(cy) * 10000 + int(cx)
                if small_icon:
                    base_order += 3_000_000_000
                elif rarity == 2:
                    base_order += 2_000_000_000
                elif rarity == 1:
                    base_order += 1_000_000_000

                calls.append(
                    _DropDrawCall(
                        x=int(cx),
                        y=int(cy),
                        size=sz,
                        tile=tile,
                        qty=qty,
                        rarity=rarity,
                        small=small_icon,
                        order=base_order,
                    )
                )

            cur_x = start_x
            for category, item_id, qty in sorted(large, key=lambda t: (-_rarity_level(t[0], t[1]), -t[2], t[1])):
                add_call(cur_x, base_y, category, item_id, qty, small_icon=False)
                cur_x += large_sz + 4

            cur_y = small_y
            for category, item_id, qty in sorted(small, key=lambda t: (-_rarity_level(t[0], t[1]), -t[2], t[1])):
                add_call(small_x, cur_y, category, item_id, qty, small_icon=True)
                cur_y += small_sz + 4

        calls.sort(key=lambda c: c.order)
        for c in calls:
            _paste_with_shadow(overlay, c.tile, (c.x, c.y), shadow=False)

            if c.qty is not None:
                text = str(c.qty)
                tx, ty = c.x + 2, c.y + 0
                _draw_text_with_stroke(
                    d,
                    (tx, ty),
                    text,
                    qty_font,
                    fill=_tile_qty_color(c.qty),
                    stroke_fill=(255, 255, 255, 220),
                    stroke=2,
                )

        scene_imgs.append(Image.alpha_composite(base_img, overlay).convert("RGB"))

    stitched = stitch_images_grid_memory(scene_imgs)
    out = io.BytesIO()
    stitched.save(out, format="PNG")
    return out.getvalue()
