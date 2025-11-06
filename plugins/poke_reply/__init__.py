# poke_reply/__init__.py
from nonebot import get_driver, logger
from .config import (
    TEXT_FILES_DIR,
    IMAGE_FILES_DIR,
    get_text_to_image_enabled_groups,
    load_config_groups
)

# vvvvvv 【修改：导入路径】 vvvvvv
from .data_manager import data_manager
from .file_monitor import file_monitor
from . import event_handlers
from . import command_handlers
from . import text_to_image
from .managers import init_management
# ^^^^^^ 【修改：导入路径】 ^^^^^^

import asyncio

# ... (文件其余部分保持不变) ...


def on_file_modified(group_id: int):
    logger.info(f"检测到群 {group_id} 的数据文件变化，重新加载数据")
    data_manager.load_text_data(group_id)
    data_manager.load_image_data(group_id)


def start_file_monitor():
    if file_monitor.start_monitoring(on_file_modified):
        logger.info("戳一戳回复插件: 文件监听器已启动")
    else:
        logger.warning("戳一戳回复插件: 文件监听器启动失败")


@get_driver().on_startup
async def init_plugin():
    TEXT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_FILES_DIR.mkdir(parents=True, exist_ok=True)

    load_config_groups()
    start_file_monitor()

    # (init_management() 会自动启动，因为它在 managers.py 中注册了 on_startup)

    logger.info("戳一戳回复插件: 初始化完成（已重构）")
    text_to_image_groups = get_text_to_image_enabled_groups()
    logger.info(f"戳一戳回复插件: 启用文本转图片的群组: {len(text_to_image_groups)} 个")
    logger.info("戳一戳回复插件: 内部CD管理已移除，请使用外部 plugin_manager 进行CD配置")


@get_driver().on_shutdown
async def shutdown_plugin():
    if file_monitor.stop_monitoring():
        logger.info("戳一戳回复插件: 文件监听器已停止")
    else:
        logger.error("戳一戳回复插件: 停止文件监听器时发生错误")