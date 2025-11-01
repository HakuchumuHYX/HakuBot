# __init__.py
from nonebot import get_driver, logger
from .config import (
    TEXT_FILES_DIR,
    IMAGE_FILES_DIR,
    get_poke_cd_enabled_groups,
    get_text_to_image_enabled_groups,
    load_config_groups
)
from .core.data_manager import data_manager
from .core.file_monitor import file_monitor
from .handlers.event_handlers import poke, contribute
from .handlers.stat_handlers import view_text_count, view_all_text_count, view_content_stats
from .services.text_to_image import (
    enable_text_to_image,
    disable_text_to_image,
    text_to_image_status,
    set_text_threshold
)
from .services.text_image_cache import text_image_cache, convert_to_text
from .services.management import (
    init_management,
    apply_delete,
    handle_delete_request,
    view_delete_requests,
    clear_processed_requests
)
from .handlers.cd_handlers import (
    enable_poke_cd,
    disable_poke_cd,
    poke_cd_status,
    set_poke_cd_time_cmd,
    view_all_cd_groups,
    view_all_text_to_image_groups
)
from .handlers.view_contributions import (
    view_all_contributions,
    view_all_texts,
    view_all_images
)
import asyncio

def on_file_modified(group_id: int):
    """文件修改时的回调函数"""
    logger.info(f"检测到群 {group_id} 的数据文件变化，重新加载数据")
    data_manager.load_text_data(group_id)
    data_manager.load_image_data(group_id)


# 启动文件监听
def start_file_monitor():
    """启动文件监听器"""
    if file_monitor.start_monitoring(on_file_modified):
        logger.info("戳一戳回复插件: 文件监听器已启动")
    else:
        logger.warning("戳一戳回复插件: 文件监听器启动失败")


# 在插件加载时启动文件监听
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    # 确保目录存在
    TEXT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    # 重新加载配置（确保最新）
    load_config_groups()

    # 启动文件监听
    start_file_monitor()

    # 初始化管理模块
    await init_management()

    logger.info("戳一戳回复插件: 初始化完成，采用多群组独立数据文件模式")
    logger.info("戳一戳回复插件: 已支持图片投稿和加权随机选择功能")
    logger.info("戳一戳回复插件: 已加载文本转图片模块")
    logger.info("戳一戳回复插件: 已加载统计功能模块")
    logger.info("戳一戳回复插件: 已加载文本图片缓存模块")
    logger.info("戳一戳回复插件: 支持回复'转文字'将图片转回文本")
    logger.info("戳一戳回复插件: 已加载管理模块，支持删除申请功能")
    logger.info("戳一戳回复插件: 已加载戳一戳CD管理模块")
    logger.info("戳一戳回复插件: 已加载查看投稿内容功能模块")

    # 显示当前启用的群组
    poke_cd_groups = get_poke_cd_enabled_groups()
    text_to_image_groups = get_text_to_image_enabled_groups()
    logger.info(f"戳一戳回复插件: 启用戳一戳CD的群组: {len(poke_cd_groups)} 个")
    logger.info(f"戳一戳回复插件: 启用文本转图片的群组: {len(text_to_image_groups)} 个")


async def start_cache_cleaner():
    """启动缓存清理定时任务"""

    async def clean_expired_cache():
        while True:
            await asyncio.sleep(300)  # 每5分钟清理一次
            text_image_cache.clean_expired_cache()

    asyncio.create_task(clean_expired_cache())


# 机器人关闭时停止监听器
@get_driver().on_shutdown
async def shutdown_plugin():
    """插件关闭"""
    if file_monitor.stop_monitoring():
        logger.info("戳一戳回复插件: 文件监听器已停止")
    else:
        logger.error("戳一戳回复插件: 停止文件监听器时发生错误")