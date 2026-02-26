from ..utils.browser import html_to_pic
from jinja2 import Template
from typing import List
from .models import EventSimple, EventDetail

# =======================
# HTML 模板 (Dark Mode + Watermark)
# =======================

LIST_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body { font-family: "Microsoft YaHei", sans-serif; background-color: #121212; padding: 20px; color: #e8e6e3; }
    .container { width: 800px; margin: 0 auto; background: #1e1e1e; border-radius: 15px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); overflow: hidden; border: 1px solid #333; }
    .header { background: linear-gradient(135deg, #005f8f 0%, #004085 100%); color: #ffffff; padding: 20px; text-align: center; border-bottom: 1px solid #333; }
    .header h1 { margin: 0; font-size: 28px; text-shadow: 0 2px 4px rgba(0,0,0,0.5); }
    .list-item { display: flex; align-items: center; border-bottom: 1px solid #2f2f2f; padding: 15px; transition: background 0.2s; }
    .list-item:nth-child(even) { background-color: #252525; }
    .id-badge { background: #000; color: #ccc; padding: 5px 10px; border-radius: 5px; font-weight: bold; min-width: 40px; text-align: center; margin-right: 15px; border: 1px solid #444; }
    .info { flex: 1; }
    .title-cn { font-size: 18px; font-weight: bold; color: #ececec; margin-bottom: 5px; }
    .title-jp { font-size: 14px; color: #999; }
    .status { font-size: 12px; padding: 3px 8px; border-radius: 10px; background: #0d2b45; color: #4db8ff; border: 1px solid #1a4d75; }

    /* 水印区域样式 */
    .footer {
        padding: 20px;
        text-align: center;
        color: #666;           /* 浅灰色 */
        font-size: 12px;
        border-top: 1px solid #333;
        background-color: #1e1e1e; /* 与卡片背景一致 */
        white-space: pre-wrap;     /* 支持换行符 \n */
        line-height: 1.6;
    }

    /* 全屏水印样式 */
    .watermark-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 9999;
    }
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
                      fill="rgba(255, 255, 255, 0.15)" 
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
        <h1>剧情活动列表 (仅显示最新20条，可搭配”查活动“命令查询指定活动ID)</h1>
    </div>
    {% for event in events %}
    <div class="list-item">
        <div class="id-badge">#{{ event.event_id }}</div>
        <div class="info">
            <div class="title-cn">{{ event.title_cn }}</div>
            <div class="title-jp">{{ event.title_jp }}</div>
        </div>
        <div class="status">{{ event.summary_status }} ({{ event.chapter_count }}章)</div>
    </div>
    {% endfor %}

    {% if watermark %}
    <div class="footer">{{ watermark }}</div>
    {% endif %}
</div>
</body>
</html>
"""

DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body { font-family: "Microsoft YaHei", sans-serif; background-color: #121212; padding: 20px; color: #e8e6e3; }
    .card { width: 700px; margin: 0 auto; background: #1e1e1e; border-radius: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.6); overflow: hidden; border: 1px solid #333; }
    .hero { background: linear-gradient(135deg, #8E2121 0%, #631D1D 100%); color: white; padding: 30px; position: relative; border-bottom: 1px solid #444; }
    .event-id { position: absolute; top: 20px; right: 20px; background: rgba(0,0,0,0.4); padding: 5px 15px; border-radius: 20px; font-weight: bold; border: 1px solid rgba(255,255,255,0.2); }
    .hero h1 { margin: 0 0 10px 0; font-size: 32px; text-shadow: 0 2px 5px rgba(0,0,0,0.8); }
    .hero h2 { margin: 0; font-size: 18px; opacity: 0.8; font-weight: normal; color: #ccc; }
    .intro { padding: 25px; border-bottom: 2px dashed #333; }
    .section-title { font-size: 18px; font-weight: bold; color: #ff6b6b; margin-bottom: 10px; border-left: 5px solid #ff6b6b; padding-left: 10px; }
    .intro-text { font-size: 14px; color: #cfcfcf; line-height: 1.6; background: #262626; padding: 15px; border-radius: 10px; border: 1px solid #333; }
    .chapters { padding: 25px; }
    .chapter-item { display: flex; margin-bottom: 25px; background: #252525; border: 1px solid #333; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.3); }
    .chapter-img { width: 280px; height: 158px; flex-shrink: 0; overflow: hidden; position: relative; }
    .chapter-img img { width: 100%; height: 100%; object-fit: cover; opacity: 0.9; transition: opacity 0.3s; }
    .chapter-img img:hover { opacity: 1; }
    .chapter-content { padding: 15px; flex: 1; display: flex; flex-direction: column; }
    .chapter-title { font-size: 16px; font-weight: bold; color: #eee; margin-bottom: 8px; }
    .chapter-summary { font-size: 13px; color: #aaa; line-height: 1.5; flex: 1; }
    .chapter-badge { display: inline-block; background: #000; color: #ccc; font-size: 12px; padding: 2px 8px; border-radius: 4px; margin-bottom: 5px; width: fit-content; border: 1px solid #444; }

    /* 水印区域样式 */
    .footer {
        padding: 20px;
        text-align: center;
        color: #666;           /* 浅灰色 */
        font-size: 12px;
        border-top: 1px solid #333;
        background-color: #1e1e1e; /* 与卡片背景一致 */
        white-space: pre-wrap;     /* 支持换行符 \n */
        line-height: 1.6;
    }

    /* 全屏水印样式 */
    .watermark-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 9999;
    }
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
                      fill="rgba(255, 255, 255, 0.15)" 
                      font-size="24" font-family="Microsoft YaHei" font-weight="bold">
                    {{ global_watermark }}
                </text>
            </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#watermark-pattern)" />
    </svg>
</div>
{% endif %}

<div class="card">
    <div class="hero">
        <div class="event-id">Event #{{ data.event_id }}</div>
        <h1>{{ data.title_cn }}</h1>
        <h2>{{ data.title_jp }}</h2>
    </div>
    <div class="intro">
        <div class="section-title">剧情梗概</div>
        <div class="intro-text">{{ data.outline_cn or data.summary_cn or "暂无简介" }}</div>
    </div>
    <div class="chapters">
        <div class="section-title">章节列表</div>
        {% for chapter in data.chapters %}
        <div class="chapter-item">
            <div class="chapter-img"><img src="{{ chapter.image_url }}" alt="cover"></div>
            <div class="chapter-content">
                <div class="chapter-badge">Chapter {{ chapter.chapter_no }}</div>
                <div class="chapter-title">{{ chapter.title_cn }}</div>
                <div class="chapter-summary">{{ chapter.summary_cn }}</div>
            </div>
        </div>
        {% endfor %}
    </div>

    {% if watermark %}
    <div class="footer">{{ watermark }}</div>
    {% endif %}
</div>
</body>
</html>
"""


async def render_event_list_pic(events: List[EventSimple], watermark: str = "", global_watermark: str = "") -> bytes:
    template = Template(LIST_TEMPLATE)
    html = template.render(events=events, watermark=watermark, global_watermark=global_watermark)
    return await html_to_pic(html=html, viewport={"width": 850, "height": 1000})


async def render_event_detail_pic(data: EventDetail, watermark: str = "", global_watermark: str = "") -> bytes:
    template = Template(DETAIL_TEMPLATE)
    html = template.render(data=data, watermark=watermark, global_watermark=global_watermark)
    return await html_to_pic(html=html, viewport={"width": 750, "height": 1000}, wait=2)
