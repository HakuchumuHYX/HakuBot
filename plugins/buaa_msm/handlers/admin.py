# plugins/buaa_msm/handlers/admin.py
"""
管理员/定时维护入口（handlers）

职责：
- 注册 apscheduler 定时清理任务（早上清文件+清访问记录；下午清文件）
- 注册管理员命令：
  - 清理文件
  - 文件统计
  - 文件列表（原先在 upload handler 中，迁移到这里）

说明：
- 具体清理/统计逻辑放在 services.maintenance_service
"""

from __future__ import annotations

from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.rule import is_type

from ..config import plugin_config
from ..infra.storage import file_storage_dir
from ..services.maintenance_service import (
    build_stats_message,
    cleanup_with_cache,
    collect_file_stats,
    format_file_size,
    list_storage_items,
)

# 导入定时任务插件
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


# ============== 定时任务 ==============


@scheduler.scheduled_job(
    "cron",
    hour=plugin_config.cleanup.morning_hour,
    minute=plugin_config.cleanup.morning_minute,
    id="cleanup_files_morning",
)
async def cleanup_files_morning():
    """早上清理任务：清文件 + 清缓存 + 清访问记录"""
    logger.info(f"执行 {plugin_config.cleanup.morning_hour}:00 文件清理任务")
    await cleanup_with_cache(clear_visit_history=True)


@scheduler.scheduled_job(
    "cron",
    hour=plugin_config.cleanup.afternoon_hour,
    minute=plugin_config.cleanup.afternoon_minute,
    id="cleanup_files_afternoon",
)
async def cleanup_files_afternoon():
    """下午清理任务：清文件 + 清缓存（不清访问记录）"""
    logger.info(f"执行 {plugin_config.cleanup.afternoon_hour}:00 文件清理任务")
    await cleanup_with_cache(clear_visit_history=False)


# ============== 管理命令 ==============


cleanup_cmd = on_command("清理文件", permission=SUPERUSER, priority=5, block=True)


@cleanup_cmd.handle()
async def handle_cleanup_command(bot: Bot, event):
    try:
        await cleanup_with_cache(clear_visit_history=True)
        await cleanup_cmd.finish("文件及访问历史清理完成")
    except Exception as e:
        logger.error(f"手动清理文件失败: {e}")
        await cleanup_cmd.finish(f"文件清理失败: {str(e)}")


stats_cmd = on_command("文件统计", permission=SUPERUSER, priority=5, block=True)


@stats_cmd.handle()
async def handle_stats_command(bot: Bot, event):
    try:
        if not file_storage_dir.exists():
            await stats_cmd.finish("文件存储目录不存在")
            return

        items = list_storage_items()
        if not items:
            await stats_cmd.finish("文件存储目录为空")
            return

        stats = collect_file_stats()
        await stats_cmd.finish(build_stats_message(stats))
    except Exception as e:
        logger.error(f"获取文件统计失败: {e}")
        await stats_cmd.finish(f"获取文件统计失败: {str(e)}")


# 文件列表：原先在 handlers/upload.py 中（SUPERUSER + 私聊）
list_files_cmd = on_command("文件列表", rule=is_type(PrivateMessageEvent), permission=SUPERUSER, priority=5, block=True)


@list_files_cmd.handle()
async def handle_list_files_command(bot: Bot, event: PrivateMessageEvent):
    try:
        files = list_storage_items()
        if not files:
            await list_files_cmd.finish("文件存储目录为空。")
            return

        file_list = []
        for i, file in enumerate(files):
            try:
                size_str = format_file_size(file.stat().st_size)
            except Exception:
                size_str = "未知大小"
            file_list.append(f"{i + 1}. {file.name} ({size_str})")

        message = f"已上传的文件 (共 {len(files)} 个):\n" + "\n".join(file_list)
        await list_files_cmd.finish(message)
    except Exception as e:
        logger.error(f"列出文件失败: {e}")
        await list_files_cmd.finish("列出文件失败。")


logger.success("管理员/维护 handlers 已加载成功！")
