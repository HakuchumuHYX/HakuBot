# stickers/help.py
import io
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import MessageSegment, GroupMessageEvent
from ..utils.image_utils import image_segment
from nonebot.exception import FinishedException
from ..plugin_manager.enable import is_plugin_enabled

# 导入共享浏览器模块
try:
    from ..utils.browser import html_to_pic
    from jinja2 import Template

    HTMLRENDER_AVAILABLE = True
except ImportError:
    logger.warning("stickers-help: 未检测到浏览器模块或 jinja2，将使用 PIL 备用方案")
    HTMLRENDER_AVAILABLE = False

# 注册帮助命令
help_matcher = on_command(
    "sticker帮助",
    aliases={"sticker help", "stickers help", "stickers帮助", "表情包帮助"},
    priority=5,
    block=True
)


# 帮助文档数据结构
HELP_DATA = [
    {
        "category": "基础功能",
        "icon": "🎲",
        "commands": [
            {"cmd": "随机<文件夹>", "desc": "发送一张指定文件夹的随机表情", "eg": "随机猫猫"},
            {"cmd": "随机<文件夹> xN", "desc": "发送 N 张指定文件夹的随机表情 (N≤5)", "eg": "随机猫猫 x3"},
            {"cmd": "随机stickers", "desc": "从所有文件夹中随机抽取一张", "eg": "随机表情"},
            {"cmd": "随机stickers xN", "desc": "从所有文件夹中随机抽取 N 张", "eg": "随机表情 x5"},
        ]
    },
    {
        "category": "查看与浏览",
        "icon": "👀",
        "commands": [
            {"cmd": "查看stickers", "desc": "显示所有文件夹列表及统计信息", "eg": "查看stickers"},
            {"cmd": "看所有<文件夹>", "desc": "生成该文件夹下所有表情的缩略图概览", "eg": "看所有miku"},
            {"cmd": "查看<编号>", "desc": "查看指定编号的表情原图", "eg": "查看947"},
        ]
    },
    {
        "category": "投稿与管理",
        "icon": "📤",
        "commands": [
            {"cmd": "<文件夹>投稿", "desc": "将图片投稿至指定文件夹 (支持查重)", "eg": "[发图] 猫猫投稿"},
            {"cmd": "<文件夹>投稿 force", "desc": "强制投稿，跳过查重检查", "eg": "[发图] 猫猫投稿 force"},
        ]
    }
]

# HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <style>
        :root {
            --primary-color: #5c7cfa;
            --primary-dark: #3b5bdb; /* 新增深色主题色 */
            --bg-color: #f8f9fa; /* 背景稍微调亮一点 */
            --card-bg: #ffffff;
            --text-main: #2c3e50;
            --text-sub: #868e96;
            /* --- 命令样式修改区域 --- */
            --cmd-bg: #e7f5ff; /* 改为极浅的蓝色背景 */
            --cmd-text: var(--primary-dark); /* 改为深蓝色，不再使用丑红 */
            --cmd-border: #d0ebff; /* 新增边框色 */
        }
        body {
            /* 使用更现代的系统字体栈 */
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans SC", sans-serif;
            background-color: var(--bg-color);
            margin: 0;
            padding: 30px;
            display: flex;
            justify-content: center;
            -webkit-font-smoothing: antialiased; /* 让字体更清晰 */
        }
        .container {
            width: 720px;
            background-color: var(--card-bg);
            border-radius: 20px; /* 更圆润一点 */
            box-shadow: 0 12px 40px rgba(0,0,0,0.08);
            overflow: hidden;
            padding-bottom: 25px;
        }
        .header {
            /* 调整渐变角度和颜色 */
            background: linear-gradient(120deg, #4dabf7 0%, #5c7cfa 100%);
            color: white;
            padding: 35px 45px;
            position: relative;
        }
        .header h1 {
            margin: 0;
            font-size: 34px;
            font-weight: 800;
            letter-spacing: -0.5px;
        }
        .header p {
            margin: 12px 0 0 0;
            opacity: 0.95;
            font-size: 17px;
            font-weight: 500;
        }
        .content {
            padding: 35px 45px;
        }
        .section {
            margin-bottom: 35px;
        }
        .section-title {
            display: flex;
            align-items: center;
            font-size: 21px;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f1f3f5;
        }
        .section-icon {
            margin-right: 12px;
            font-size: 26px;
        }
        .command-item {
            display: flex;
            flex-direction: column;
            margin-bottom: 18px;
            padding: 14px 18px;
            background: #fff;
            border-radius: 12px;
            border: 1px solid #e9ecef;
            box-shadow: 0 2px 6px rgba(0,0,0,0.02); /* 轻微的卡片阴影 */
            transition: all 0.2s;
        }
        .command-header {
            display: flex;
            justify-content: flex-start; /* 左对齐 */
            align-items: center;
            margin-bottom: 8px;
        }
        /* --- 重点修改区域：命令样式 --- */
        .cmd-code {
            /* 1. 弃用 monospace，继承 body 的无衬线字体，解决间距过大问题 */
            font-family: inherit; 
            background-color: var(--cmd-bg);
            color: var(--cmd-text);
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 15px;
            /* 2. 调整字重，看起来更精致 */
            font-weight: 600; 
            /* 3. 增加轻微边框和阴影，增加立体感 */
            border: 1px solid var(--cmd-border);
            box-shadow: 0 1px 2px rgba(59, 91, 219, 0.05);
            letter-spacing: -0.2px; /* 微调字间距使其更紧凑 */
        }
        .cmd-desc {
            font-size: 15px;
            color: var(--text-main);
            margin-top: 4px;
            line-height: 1.5;
        }
        .cmd-eg {
            font-size: 13px;
            color: var(--text-sub);
            margin-top: 6px;
            background-color: #f8f9fa;
            padding: 4px 8px;
            border-radius: 6px;
            display: inline-block; /* 让示例也像一个小标签 */
        }
        .cmd-eg::before {
            content: "e.g. ";
            font-weight: 600;
            color: #adb5bd;
        }
        .footer {
            text-align: center;
            color: #adb5bd;
            font-size: 13px;
            margin-top: 30px;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Stickers Plugin</h1>
            <p>表情包管理插件帮助文档</p>
        </div>
        <div class="content">
            {% for section in help_data %}
            <div class="section">
                <div class="section-title">
                    <span class="section-icon">{{ section.icon }}</span>
                    <span>{{ section.category }}</span>
                </div>
                {% for item in section.commands %}
                <div class="command-item">
                    <div class="command-header">
                        <span class="cmd-code">{{ item.cmd }}</span>
                    </div>
                    <div class="cmd-desc">{{ item.desc }}</div>
                    <div class="cmd-eg">{{ item.eg }}</div>
                </div>
                {% endfor %}
            </div>
            {% endfor %}

            <div class="footer">
                Generated by HakuBot
            </div>
        </div>
    </div>
</body>
</html>
"""

@help_matcher.handle()
async def handle_help(event: GroupMessageEvent):
    """处理帮助命令，发送图片"""
    if not is_plugin_enabled("stickers", str(event.group_id), str(event.user_id)):
        return

    try:
        if HTMLRENDER_AVAILABLE:
            image_data = await render_help_html()
        else:
            image_data = await render_help_text_fallback()

        if image_data:
            await help_matcher.finish(image_segment(image_data))
        else:
            await help_matcher.finish("帮助图片生成失败，请检查日志。")

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"stickers-help: 发送帮助时出错: {e}")
        await help_matcher.finish(f"获取帮助信息时出错: {e}")


async def render_help_html() -> bytes:
    """使用 htmlrender + jinja2 渲染精美 HTML"""
    try:
        # 1. 手动使用 Jinja2 渲染模板字符串
        template = Template(HTML_TEMPLATE)
        html_content = template.render(help_data=HELP_DATA)

        # 2. 将渲染好的 HTML 字符串传递给 html_to_pic
        return await html_to_pic(
            html=html_content,
            viewport={"width": 750, "height": 1000}  # 宽度固定，高度自适应
        )
    except Exception as e:
        logger.warning(f"htmlrender 渲染失败: {e}，尝试使用 PIL 回退")
        return await render_help_text_fallback()


async def render_help_text_fallback() -> bytes:
    """
    [备用方案] 将 HELP_DATA 转换为文本并使用 PIL 渲染
    """
    text_content = "Stickers 插件帮助文档\n---------------------------------\n"

    for section in HELP_DATA:
        text_content += f"\n[{section['category']}]\n"
        for idx, item in enumerate(section['commands'], 1):
            text_content += f"{idx}. {item['cmd']}\n"
            text_content += f"   - 功能: {item['desc']}\n"
            text_content += f"   - 示例: {item['eg']}\n"

    # 2. 调用 PIL 绘图
    return await fallback_text_to_image_engine(text_content)


async def fallback_text_to_image_engine(text: str) -> bytes:
    """PIL 绘图引擎"""
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_size = 20
        line_spacing = 10
        margin = 40
        max_width = 800

        font = None
        _font_candidates = [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "msyh.ttc",
            "simhei.ttf",
        ]
        for _fc in _font_candidates:
            try:
                font = ImageFont.truetype(_fc, font_size)
                break
            except:
                continue
        if font is None:
            font = ImageFont.load_default()

        # 简单分行
        lines = text.strip().split('\n')

        # 计算画布大小
        line_height = font_size + line_spacing
        img_height = len(lines) * line_height + 2 * margin

        # 计算最大宽度
        max_line_width = 0
        for line in lines:
            try:
                if hasattr(font, "getbbox"):
                    bbox = font.getbbox(line)
                    w = bbox[2] - bbox[0]
                else:
                    w = font.getsize(line)[0]
            except:
                w = len(line) * font_size * 0.6
            max_line_width = max(max_line_width, w)

        img_width = max(max_width, int(max_line_width + 2 * margin))

        # 绘图
        img = Image.new('RGB', (img_width, img_height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        y = margin
        for line in lines:
            draw.text((margin, y), line, fill=(0, 0, 0), font=font)
            y += line_height

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG', optimize=True)
        return img_bytes.getvalue()

    except Exception as e:
        logger.error(f"stickers-help: PIL 绘图失败: {e}")
        return b""
