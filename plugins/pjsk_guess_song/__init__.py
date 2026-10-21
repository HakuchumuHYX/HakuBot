from core.lifecycle import runtime as app_runtime, on_plugin_startup, on_plugin_shutdown
import asyncio
from nonebot import get_driver, logger
from utils.paths import sekai_master_dir
from plugins.pjsk_guess_song.tools.generate_guess_song import (
    generate as generate_guess_song,
)
from plugins.pjsk_guess_song.runtime import (
    resources_dir,
    db_service,
    cache_service,
    plugin_config,
)

driver = get_driver()

MASTERDATA_DIR = str(sekai_master_dir() / "master")


@on_plugin_startup(driver, "pjsk_guess_song")
async def _on_startup():
    """Nonebot 启动时执行异步初始化"""
    await db_service.init_db()

    # 从 masterdata 重新生成 guess_song.json
    guess_song_output = str(resources_dir / "guess_song.json")
    try:
        success = await asyncio.to_thread(
            generate_guess_song, MASTERDATA_DIR, guess_song_output
        )
        if not success:
            logger.warning("guess_song.json 生成失败，将尝试使用已有文件。")
    except Exception:
        logger.opt(exception=True).warning("生成 guess_song.json 时出错，将尝试使用已有文件。")

    await cache_service.load_resources_and_manifest()
    app_runtime.spawn(cache_service.periodic_cleanup_task(), name="pjsk_guess_song")
    logger.info(
        f"PJSK 猜歌已启动：{len(cache_service.song_data)} 首歌曲、"
        f"{len(cache_service.song_aliases)} 个别名，"
        f"{'Full' if plugin_config.full_mode else 'Light'} 模式"
    )


@on_plugin_shutdown(driver, "pjsk_guess_song")
async def _on_shutdown():
    """Nonebot 关闭时执行清理"""
    await cache_service.terminate()
    logger.debug("PJSK 猜歌插件服务已终止。")


# --- 5. 导入处理器模块以注册 Matcher ---
# 导入时，它们会从本文件导入已实例化的 `game_service`, `image_service` 等
from plugins.pjsk_guess_song import game_session  # noqa: F401
from plugins.pjsk_guess_song.handlers import game, listen, other, leaderboard  # noqa: F401
