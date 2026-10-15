from __future__ import annotations

try:
    from nonebot import get_driver

    get_driver()
except Exception:
    pass
else:
    from plugins.pixiv_id_fetcher import matcher
