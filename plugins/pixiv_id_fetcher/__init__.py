from __future__ import annotations

__plugin_name__ = "Pixiv ID取图"
__plugin_usage__ = """
发送 pixiv <pid> 获取 Pixiv 作品图片
发送 pid <pid> 或 p站图 <pid> 也可以触发
""".strip()

try:
    from nonebot import get_driver

    get_driver()
except Exception:
    pass
else:
    from .matcher import *  # noqa: F401,F403
