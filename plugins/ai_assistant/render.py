"""把 LLM 回复的 Markdown 渲染成图片（templates/reply.html）"""

from pathlib import Path

import markdown

from plugins.ai_assistant.config import plugin_config
from utils.rendering.engine import render_template

TEMPLATE_DIR = Path(__file__).parent / "templates"


async def render_reply(md: str) -> bytes:
    """GitHub 样式；回复里允许内联 HTML，水印就是一段 <div>"""
    html = markdown.markdown(
        md,
        extensions=[
            "pymdownx.tasklist",
            "tables",
            "fenced_code",
            "codehilite",
            "mdx_math",
            "pymdownx.tilde",
        ],
        extension_configs={"mdx_math": {"enable_dollar_delimiter": True}},
    )
    return await render_template(
        TEMPLATE_DIR,
        "reply.html",
        width=800,
        md=html,
        # 只有含公式时才加载 KaTeX，脚本和字体一共 1.7MB
        math="math/tex" in html,
        bg_color=plugin_config.chat.bg_color,
    )
