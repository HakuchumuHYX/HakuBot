from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ================= 资源配置 =================
PLUGIN_DIR = Path(__file__).parent
RES_DIR = PLUGIN_DIR / "resources"
RES_DIR.mkdir(parents=True, exist_ok=True)

# 字体路径
FONT_FILE = RES_DIR / "font.ttf"

# 画布尺寸
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 600

# ================= 样式配置 (主题定义) =================

# 白天模式配色 (AliceBlue清新风)
THEME_DAY = {
    "bg": "#f0f8ff",  # 淡蓝色背景
    "title": "#2c3e50",  # 深青灰色标题
    "text_main": "#333333",  # 深灰色正文
    "text_sub": "#666666",  # 中灰色副文
    "watermark": "#aaaaaa"  # 浅灰色水印
}

# 夜间模式配色 (暗黑极客风)
THEME_NIGHT = {
    "bg": "#2b2b2b",  # 深灰黑色背景
    "title": "#00d1b2",  # 青色高亮标题 (Cyberpunk feel)
    "text_main": "#e0e0e0",  # 亮白色正文
    "text_sub": "#aaaaaa",  # 浅灰色副文
    "watermark": "#555555"  # 深灰色水印 (低调)
}


def get_font(size: int):
    """加载字体"""
    if FONT_FILE.exists():
        try:
            return ImageFont.truetype(str(FONT_FILE), size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text_centered(draw, text, font, center_x, start_y, fill):
    """绘制居中文字"""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]

    x = center_x - text_width / 2
    y = start_y

    draw.text((x, y), text, font=font, fill=fill)

    # 返回文字高度
    return bbox[3] - bbox[1]


def draw_alive_card(
        current_time: str,
        current_since: str,
        total_time: str,
        total_since: str,
        watermark: str,
        is_night: bool = False  # <--- 新增参数
) -> bytes:
    """
    绘制状态卡片 (支持日夜模式切换)
    """

    # 1. 确定主题颜色
    theme = THEME_NIGHT if is_night else THEME_DAY

    # 2. 创建背景
    image = Image.new("RGBA", (DEFAULT_WIDTH, DEFAULT_HEIGHT), theme["bg"])
    draw = ImageDraw.Draw(image)

    width, height = image.size
    center_x = width / 2

    # 3. 动态计算字体大小
    title_size = int(height * 0.09)
    label_size = int(height * 0.04)
    value_size = int(height * 0.07)
    since_size = int(height * 0.035)
    footer_size = int(height * 0.03)

    font_title = get_font(title_size)
    font_label = get_font(label_size)
    font_value = get_font(value_size)
    font_since = get_font(since_size)
    font_footer = get_font(footer_size)

    # 4. 绘制内容

    # --- 标题 ---
    current_y = height * 0.12
    h = draw_text_centered(draw, "SYSTEM STATUS", font_title, center_x, current_y, theme["title"])
    current_y += h + height * 0.08

    # --- 第一块：Current Session ---
    draw_text_centered(draw, "Current Session", font_label, center_x, current_y, theme["text_sub"])
    current_y += label_size * 1.8

    draw_text_centered(draw, current_time, font_value, center_x, current_y, theme["text_main"])
    current_y += value_size * 1.3

    draw_text_centered(draw, f"Since {current_since}", font_since, center_x, current_y, theme["text_sub"])

    current_y += since_size + height * 0.08

    # --- 第二块：Total Runtime ---
    draw_text_centered(draw, "Total Runtime", font_label, center_x, current_y, theme["text_sub"])
    current_y += label_size * 1.8

    draw_text_centered(draw, total_time, font_value, center_x, current_y, theme["text_main"])
    current_y += value_size * 1.3

    draw_text_centered(draw, f"Since {total_since}", font_since, center_x, current_y, theme["text_sub"])

    # 5. 绘制底部水印
    bbox = draw.textbbox((0, 0), watermark, font=font_footer)
    w_width = bbox[2] - bbox[0]
    w_height = bbox[3] - bbox[1]

    x_pos = width - w_width - 30
    y_pos = height - w_height - 30

    draw.text(
        (x_pos, y_pos),
        watermark,
        font=font_footer,
        fill=theme["watermark"],
        align="right"
    )

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
