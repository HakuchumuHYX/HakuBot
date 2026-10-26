from pathlib import Path
from utils.rendering.engine import render_template
import re

TEMPLATE_DIR = Path(__file__).parent / "templates"


def number_to_chinese(n: int) -> str:
    """
    将数字转换为中文数字（支持1-100）
    例如: 1 -> 一, 10 -> 十, 11 -> 十一, 21 -> 二十一, 100 -> 一百
    """
    if n == 100:
        return "一百"

    chars = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if n < 10:
        return chars[n]
    elif n < 20:
        return "十" + chars[n % 10]
    else:
        tens = n // 10
        unit = n % 10
        return chars[tens] + "十" + chars[unit]


def parse_sign_text(text: str) -> dict:
    """解析签文文本结构"""
    if not text:
        return {"is_empty": True}

    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return {"is_empty": True}

    title = lines[0]

    poem_end_idx = 5 if len(lines) >= 5 else len(lines)
    poem = lines[1:poem_end_idx]
    rest_lines = lines[poem_end_idx:] if len(lines) > poem_end_idx else []

    intro_data = []
    items = []
    item_pattern = re.compile(r"^(.+?)[：:](.+)$")
    poem_set = set(poem)

    for line in rest_lines:
        match = item_pattern.match(line)
        if match:
            items.append({"key": match.group(1), "value": match.group(2)})
        else:
            is_quote = line in poem_set
            intro_data.append({"text": line, "is_quote": is_quote})

    return {
        "is_empty": False,
        "title": title,
        "poem": poem,
        "intro_data": intro_data,
        "items": items,
    }


async def render_sign_image_v2(
    sign_text: str, index: int, watermark_text: str = ""
) -> bytes:
    """
    渲染签文图片
    :param sign_text: 签文内容
    :param index: 签文的索引 (0-based)，如果是-1则代表空签
    :param watermark_text: 水印文字
    """
    if index < 0 or sign_text == "空签":
        context = {"is_empty": True}
    else:
        context = parse_sign_text(sign_text)
        # 0 -> 第一番, 1 -> 第二番
        cn_num = number_to_chinese(index + 1)
        context["number_str"] = f"第{cn_num}番"

    context["watermark_text"] = watermark_text

    return await render_template(TEMPLATE_DIR, "sign.html", width=420, **context)
