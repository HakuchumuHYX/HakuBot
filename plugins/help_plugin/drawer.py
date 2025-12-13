from pathlib import Path
from nonebot_plugin_htmlrender import template_to_pic
from .config import HelpConfig

# 模板目录路径
TEMPLATE_PATH = Path(__file__).parent / "templates"

async def render_help_image(config: HelpConfig, is_dark: bool) -> bytes:
    """
    使用 htmlrender 渲染图片
    :param config: 配置对象
    :param is_dark: 是否为暗黑模式
    """
    return await template_to_pic(
        template_path=str(TEMPLATE_PATH),
        template_name="help.html",
        templates={
            "help_text": config.help_text,
            "is_dark": is_dark,  # 传入模板变量
        },
        pages={
            "viewport": {"width": 650, "height": 100},
            "base_url": f"file://{TEMPLATE_PATH}"
        }
    )