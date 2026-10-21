from core.lifecycle import on_plugin_startup, on_plugin_shutdown
from nonebot import get_driver, logger

# 导入所有 Handlers 以注册事件响应器
from plugins.poke_reply.handlers import poke  # noqa: F401
from plugins.poke_reply.handlers import contribute  # noqa: F401
from plugins.poke_reply.handlers import management  # noqa: F401
from plugins.poke_reply.handlers import view  # noqa: F401
from plugins.poke_reply.handlers import stats  # noqa: F401

# 导入文件监听器
from plugins.poke_reply.file_monitor import file_monitor
from plugins.poke_reply.models.cache import message_cache, text_image_cache
from plugins.poke_reply.models.request import delete_request_manager
from plugins.poke_reply.services.health import (
    log_health_report,
    scan_poke_reply_data_health,
)
from plugins.poke_reply.services.image import clean_expired_hash_cache


driver = get_driver()


def cleanup_expired_runtime_state():
    result = {
        "message_cache": message_cache.clean_expired_cache(),
        "text_image_cache": text_image_cache.clean_expired_cache(),
        "delete_requests": delete_request_manager.clean_expired_requests(),
        "image_hash_cache": clean_expired_hash_cache(),
    }
    logger.debug(
        "Poke Reply 启动清理完成: "
        f"消息缓存 {result['message_cache']} 条, "
        f"文本图片缓存 {result['text_image_cache']} 条, "
        f"删除申请 {result['delete_requests']} 条, "
        f"图片哈希缓存 {result['image_hash_cache']} 条"
    )
    return result


@on_plugin_startup(driver, "poke_reply")
async def startup():
    logger.debug("正在启动 Poke Reply 插件...")
    try:
        cleanup_expired_runtime_state()
    except Exception:
        logger.exception("Poke Reply 启动清理失败")
    try:
        log_health_report(scan_poke_reply_data_health())
    except Exception:
        logger.exception("Poke Reply 数据健康检查失败")
    # 启动结果由 file_monitor 自己记
    file_monitor.start_monitoring()


@on_plugin_shutdown(driver, "poke_reply")
async def shutdown():
    logger.debug("正在停止 Poke Reply 插件...")
    if file_monitor.stop_monitoring():
        logger.debug("Poke Reply 文件监听器已停止")
