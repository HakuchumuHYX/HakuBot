# services/management.py
import asyncio
from nonebot import logger, get_driver

from ..managers.cache_manager import message_cache
from ..managers.delete_request_manager import delete_request_manager
from ..managers.poke_cd_manager import poke_cd_manager
from ..handlers.command_handlers import (
    apply_delete,
    handle_delete_request,
    view_delete_requests,
    clear_processed_requests
)

async def start_cache_cleaner():
    """启动缓存清理定时任务"""

    async def clean_expired_cache():
        while True:
            await asyncio.sleep(300)  # 每5分钟清理一次
            # 清理过期消息缓存
            message_cache.clean_expired_cache()
            # 清理过期删除申请（超过24小时的已处理申请）
            current_time = asyncio.get_event_loop().time()
            expired_requests = []
            for request_id, request_data in delete_request_manager.requests_data.items():
                if (request_data["status"] != "pending" and
                        current_time - request_data.get("process_time", 0) > 86400):  # 24小时
                    expired_requests.append(request_id)

            for request_id in expired_requests:
                delete_request_manager.remove_request(request_id)

            if expired_requests:
                logger.info(f"清理了 {len(expired_requests)} 个过期的删除申请")

            # 新增：清理过期戳一戳CD记录
            poke_cd_manager.clear_expired_cd()

            logger.debug("已执行定时缓存清理")

    asyncio.create_task(clean_expired_cache())


# 在插件启动时启动清理任务
@get_driver().on_startup
async def init_management():
    """管理模块初始化"""
    await start_cache_cleaner()
    logger.info("管理模块初始化完成")