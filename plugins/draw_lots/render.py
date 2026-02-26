from ..utils.browser import html_to_pic
from jinja2 import Template
from typing import List, Dict, Any
import re

# HTML 模板
SIGN_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;700;900&display=swap');

        body {
            margin: 0;
            font-family: 'Noto Serif SC', 'SimSun', serif;
            background-color: transparent;
        }

        .container {
            background-color: #f8f7f2;
            background-image: linear-gradient(0deg, transparent 24%, rgba(0, 0, 0, .03) 25%, rgba(0, 0, 0, .03) 26%, transparent 27%, transparent 74%, rgba(0, 0, 0, .03) 75%, rgba(0, 0, 0, .03) 76%, transparent 77%, transparent), linear-gradient(90deg, transparent 24%, rgba(0, 0, 0, .03) 25%, rgba(0, 0, 0, .03) 26%, transparent 27%, transparent 74%, rgba(0, 0, 0, .03) 75%, rgba(0, 0, 0, .03) 76%, transparent 77%, transparent);
            background-size: 50px 50px;
            border: 1px solid #aaa;
            padding: 25px;
            box-shadow: 5px 5px 15px rgba(0,0,0,0.2);
            position: relative;
            width: auto; 
            box-sizing: border-box;
        }

        .border-inner {
            border: 2px solid #333;
            padding: 20px;
            position: relative;
            min-height: 600px; 
        }

        /* 标题部分 */
        .header {
            text-align: center;
            margin-bottom: 25px;
            border-bottom: 3px double #d02c2c;
            padding-bottom: 15px;
        }

        .header .number {
            font-size: 14px;
            color: #555;
            margin-bottom: 5px;
            letter-spacing: 2px;
        }

        .header .title {
            font-size: 56px;
            font-weight: 700;
            color: #d02c2c;
            line-height: 1.2;
        }

        /* 核心诗句部分 */
        .poem-box {
            text-align: center;
            font-size: 26px;
            font-weight: bold;
            color: #222;
            margin: 20px 0 30px 0;
            padding: 20px 10px;
            background-color: rgba(255, 255, 255, 0.6);
            border-top: 1px solid #ccc;
            border-bottom: 1px solid #ccc;
            letter-spacing: 2px;
        }

        .poem-line {
            margin: 8px 0;
        }

        /* 详细解释部分 */
        .explanation {
            font-size: 15px;
            line-height: 1.6;
            color: #444;
            text-align: justify;
            margin-bottom: 25px;
        }

        /* 解释中的引用诗句 - 加粗样式 */
        .explanation .quote {
            font-weight: 900;
            color: #111;
            font-size: 18px;
            margin-top: 20px;
            margin-bottom: 5px;
            letter-spacing: 1px;
        }

        /* 解释中的普通文本 */
        .explanation .text {
            margin-top: 0;
            margin-bottom: 10px;
            color: #555;
        }

        /* 运势项目列表 */
        .items-grid {
            display: flex;
            flex-direction: column;
            gap: 12px;
            font-size: 15px;
            border-top: 2px solid #eee;
            padding-top: 20px;
            padding-bottom: 40px; 
        }

        .item-row {
            display: flex;
            align-items: baseline; 
        }

        .item-label {
            font-weight: bold;
            color: #d02c2c;
            width: 130px; 
            flex-shrink: 0;
            text-align: left;
        }

        .item-content {
            color: #333;
            flex-grow: 1;
        }

        /* 空签样式 */
        .empty-sign {
            text-align: center;
            padding: 100px 0;
            color: #888;
        }

        .footer {
            margin-top: 30px;
            text-align: center;
            font-size: 12px;
            color: #aaa;
            border-top: 1px dashed #ccc;
            padding-top: 10px;
            padding-bottom: 20px;
        }

        /* 水印样式 */
        .watermark {
            position: absolute;
            bottom: 10px;
            right: 15px;
            font-size: 14px;
            color: rgba(0, 0, 0, 0.15);
            font-weight: bold;
            font-family: sans-serif;
            pointer-events: none;
            user-select: none;
            z-index: 10;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="border-inner">
            {% if is_empty %}
                <div class="header">
                    <div class="title" style="color: #555; font-size: 40px;">空签</div>
                </div>
                <div class="empty-sign">
                    <h3>心诚则灵</h3>
                    <p>这也是一种修行</p>
                    <p>请明天再来吧</p>
                </div>
            {% else %}
                <div class="header">
                    <div class="number">{{ number_str }} 赛博浅草寺</div>
                    <div class="title">{{ title }}</div>
                </div>

                <div class="poem-box">
                    {% for line in poem %}
                    <div class="poem-line">{{ line }}</div>
                    {% endfor %}
                </div>

                <div class="explanation">
                    {% for item in intro_data %}
                        {% if item.is_quote %}
                        <div class="quote">{{ item.text }}</div>
                        {% else %}
                        <div class="text">{{ item.text }}</div>
                        {% endif %}
                    {% endfor %}
                </div>

                {% if items %}
                <div class="items-grid">
                    {% for item in items %}
                    <div class="item-row">
                        <div class="item-label">{{ item.key }}</div>
                        <div class="item-content">{{ item.value }}</div>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            {% endif %}

            <div class="footer">
                此签仅供娱乐，命运掌握在自己手中
            </div>

            {% if watermark_text %}
            <div class="watermark">
                {{ watermark_text }}
            </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""


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


def parse_sign_text(text: str) -> Dict:
    """解析签文文本结构"""
    if not text:
        return {"is_empty": True}

    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
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
            intro_data.append({
                "text": line,
                "is_quote": is_quote
            })

    return {
        "is_empty": False,
        "title": title,
        "poem": poem,
        "intro_data": intro_data,
        "items": items
    }


async def render_sign_image_v2(sign_text: str, index: int, watermark_text: str = "") -> bytes:
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

    html = Template(SIGN_TEMPLATE).render(**context)

    return await html_to_pic(
        html,
        viewport={"width": 420, "height": 10},
        device_scale_factor=2
    )
