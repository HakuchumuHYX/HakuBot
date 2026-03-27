from __future__ import annotations

import time

from nonebot import require
from nonebot.log import logger

from .config import DATA_DIR, FILE_CLEAN_SECONDS

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler


@scheduler.scheduled_job("cron", hour=4, minute=0, id="clean_sekai_cache")
async def clean_cache() -> None:
    logger.info("开始清理 cnsk_predict 缓存...")
    count = 0
    current_time = time.time()
    for file in DATA_DIR.iterdir():
        if file.is_file() and file.suffix.lower() in [".png", ".jpg", ".jpeg"]:
            if current_time - file.stat().st_mtime > FILE_CLEAN_SECONDS:
                try:
                    file.unlink()
                    count += 1
                except Exception:
                    logger.exception(f"删除缓存失败: {file}")
    logger.info(f"cnsk_predict 缓存清理完成，共删除 {count} 张过期图片。")
