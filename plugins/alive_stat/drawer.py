from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# ================= 资源配置 =================
PLUGIN_DIR = Path(__file__).parent
RES_DIR = PLUGIN_DIR / "resources"
RES_DIR.mkdir(parents=True, exist_ok=True)

# 字体路径 (如果找不到则使用默认)
FONT_FILE = RES_DIR / "font.ttf"

# 画布尺寸 (高度增加以容纳更多留白)
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 600

# ================= 样式配置 =================
# 颜色定义
COLOR_BG = "#f0f8ff"  # 背景：淡蓝色 (AliceBlue)
COLOR_TITLE = "#2c3e50"  # 标题：深青灰色
COLOR_TEXT_MAIN = "#333333"  # 正文：深灰色
COLOR_TEXT_SUB = "#666666"  # 副文/Since：中灰色
COLOR_WATERMARK = "#aaaaaa"  # 水印：浅灰色


def get_font(size: int):
    """加载字体"""
    if FONT_FILE.exists():
        try:
            return ImageFont.truetype(str(FONT_FILE), size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_text_centered(draw, text, font, center_x, start_y, fill):
    """
    绘制居中文字
    去掉描边，仅保留纯色填充
    """
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
        watermark: str
) -> bytes:
    """
    绘制清新风格的状态卡片
    """

    # 1. 创建背景 (纯色淡蓝)
    image = Image.new("RGBA", (DEFAULT_WIDTH, DEFAULT_HEIGHT), COLOR_BG)
    draw = ImageDraw.Draw(image)

    width, height = image.size
    center_x = width / 2

    # 2. 动态计算字体大小
    # 稍微调小一点字体，配合大间距
    title_size = int(height * 0.09)  # 标题
    label_size = int(height * 0.04)  # "Current Session" 标签
    value_size = int(height * 0.07)  # 时间数值
    since_size = int(height * 0.035)  # Since 日期
    footer_size = int(height * 0.03)  # 水印

    font_title = get_font(title_size)
    font_label = get_font(label_size)
    font_value = get_font(value_size)
    font_since = get_font(since_size)
    font_footer = get_font(footer_size)

    # 3. 绘制内容

    # --- 标题 ---
    # 起始位置下移，留出顶部空间
    current_y = height * 0.12
    h = draw_text_centered(draw, "SYSTEM STATUS", font_title, center_x, current_y, COLOR_TITLE)

    # 标题与第一块内容的间距
    current_y += h + height * 0.08

    # --- 第一块：Current Session ---
    draw_text_centered(draw, "Current Session", font_label, center_x, current_y, COLOR_TEXT_SUB)
    current_y += label_size * 1.8  # 标签与数值的间距

    draw_text_centered(draw, current_time, font_value, center_x, current_y, COLOR_TEXT_MAIN)
    current_y += value_size * 1.3  # 数值与Since的间距

    draw_text_centered(draw, f"Since {current_since}", font_since, center_x, current_y, COLOR_TEXT_SUB)

    # 两大块内容之间的间距 (拉大)
    current_y += since_size + height * 0.08

    # --- 第二块：Total Runtime ---
    draw_text_centered(draw, "Total Runtime", font_label, center_x, current_y, COLOR_TEXT_SUB)
    current_y += label_size * 1.8

    draw_text_centered(draw, total_time, font_value, center_x, current_y, COLOR_TEXT_MAIN)
    current_y += value_size * 1.3

    draw_text_centered(draw, f"Since {total_since}", font_since, center_x, current_y, COLOR_TEXT_SUB)

    # 4. 绘制底部水印 (右下角，浅灰)
    bbox = draw.textbbox((0, 0), watermark, font=font_footer)
    w_width = bbox[2] - bbox[0]
    w_height = bbox[3] - bbox[1]

    # 距离右下角的边距
    margin_right = 30
    margin_bottom = 30

    x_pos = width - w_width - margin_right
    y_pos = height - w_height - margin_bottom

    draw.text(
        (x_pos, y_pos),
        watermark,
        font=font_footer,
        fill=COLOR_WATERMARK,  # 使用浅灰色
        align="right"
    )

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
