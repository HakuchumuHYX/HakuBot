from __future__ import annotations

from jinja2 import Template

from ..utils.browser import html_to_pic
from .models import MangaSimple

LIST_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body { font-family: "Microsoft YaHei", sans-serif; background-color: #111111; padding: 20px; color: #e8e6e3; }
    .container { width: 820px; margin: 0 auto; background: #191919; border-radius: 16px; overflow: hidden; border: 1px solid #303030; box-shadow: 0 8px 24px rgba(0,0,0,0.45); }
    .header { background: linear-gradient(135deg, #c95f1d 0%, #9e3f0b 100%); color: #fff; padding: 22px; text-align: center; }
    .header h1 { margin: 0; font-size: 28px; }
    .sub { margin-top: 6px; font-size: 13px; opacity: 0.85; }
    .list-item { display: flex; align-items: center; gap: 16px; padding: 16px 20px; border-top: 1px solid #2a2a2a; }
    .list-item:nth-child(even) { background: #202020; }
    .id-badge { min-width: 64px; text-align: center; padding: 6px 10px; border-radius: 999px; background: #000; color: #ffcf99; border: 1px solid #4c3726; font-weight: bold; }
    .title { flex: 1; font-size: 18px; color: #f2f2f2; }
    .status { font-size: 12px; color: #d8b188; background: #2a2016; border: 1px solid #5a4126; border-radius: 999px; padding: 4px 10px; }
    .footer { padding: 18px 20px; text-align: center; color: #666; font-size: 12px; border-top: 1px solid #303030; background: #191919; white-space: pre-wrap; line-height: 1.6; }
    .watermark-overlay { position: absolute; inset: 0; pointer-events: none; z-index: 9999; }
</style>
</head>
<body style="position: relative;">
{% if global_watermark %}
<div class="watermark-overlay">
    <svg width="100%" height="100%">
        <defs>
            <pattern id="watermark-pattern" width="400" height="300" patternUnits="userSpaceOnUse">
                <text x="200" y="150" text-anchor="middle" dominant-baseline="middle"
                      transform="rotate(-30, 200, 150)"
                      fill="rgba(255, 255, 255, 0.14)"
                      font-size="24" font-family="Microsoft YaHei" font-weight="bold">
                    {{ global_watermark }}
                </text>
            </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#watermark-pattern)" />
    </svg>
</div>
{% endif %}
<div class="container">
    <div class="header">
        <h1>漫画列表</h1>
        <div class="sub">仅显示最新 20 条，可使用“看漫画 ID”查看指定漫画</div>
    </div>
    {% for manga in mangas %}
    <div class="list-item">
        <div class="id-badge">#{{ manga.id }}</div>
        <div class="title">{{ manga.title }}</div>
        <div class="status">已收录</div>
    </div>
    {% endfor %}
    {% if watermark %}
    <div class="footer">{{ watermark }}</div>
    {% endif %}
</div>
</body>
</html>
"""


async def render_manga_list_pic(
    mangas: list[MangaSimple],
    watermark: str = "",
    global_watermark: str = "",
) -> bytes:
    template = Template(LIST_TEMPLATE)
    html = template.render(mangas=mangas, watermark=watermark, global_watermark=global_watermark)
    return await html_to_pic(html=html, viewport={"width": 860, "height": 1000})
