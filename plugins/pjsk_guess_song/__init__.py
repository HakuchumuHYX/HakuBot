# pjsk_guess_song/__init__.py

import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from nonebot import get_driver
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from .config import plugin_config, data_dir
from .services.db_service import DBService
from .services.cache_service import CacheService
from .services.audio_processor import AudioProcessor
from .services.image_service import ImageService
from .services.game_service import GameService
from .tools.generate_guess_song import generate as generate_guess_song


PLUGIN_VERSION = "1.1.3"
plugin_dir = Path(__file__).parent
resources_dir = data_dir / "resources"
output_dir = data_dir / "output"
output_dir.mkdir(parents=True, exist_ok=True)

db_path = data_dir / "guess_song_data.db"

# 实例化所有服务
db_service = DBService(str(db_path))
cache_service = CacheService(resources_dir, output_dir, plugin_config)

# 创建一个共享的线程池
executor = ThreadPoolExecutor(max_workers=5)

# 实例化新的子服务
audio_processor = AudioProcessor(cache_service, output_dir, executor)

image_service = ImageService(cache_service, resources_dir, output_dir, PLUGIN_VERSION, executor, plugin_config)

game_service = GameService(cache_service, plugin_config, audio_processor, PLUGIN_VERSION)


driver = get_driver()

# masterdata 源目录
MASTERDATA_DIR = str(Path(__file__).parent.parent.parent.parent / "haruki-sekai-master" / "master")

@driver.on_startup
async def _on_startup():
    """Nonebot 启动时执行异步初始化"""
    await db_service.init_db()

    # 从 masterdata 重新生成 guess_song.json
    guess_song_output = str(resources_dir / "guess_song.json")
    loop = asyncio.get_running_loop()
    try:
        success = await loop.run_in_executor(executor, generate_guess_song, MASTERDATA_DIR, guess_song_output)
        if not success:
            logger.warning("guess_song.json 生成失败，将尝试使用已有文件。")
    except Exception as e:
        logger.warning(f"生成 guess_song.json 时出错: {e}，将尝试使用已有文件。")

    await cache_service.load_resources_and_manifest()
    asyncio.create_task(cache_service.periodic_cleanup_task())
    logger.info("PJSK 猜歌插件服务已启动。")


@driver.on_shutdown
async def _on_shutdown():
    """Nonebot 关闭时执行清理"""
    await audio_processor.terminate()
    await cache_service.terminate()
    executor.shutdown(wait=False)
    logger.info("PJSK 猜歌插件服务已终止。")


# --- 5. 导入处理器模块以注册 Matcher ---
# 导入时，它们会从本文件导入已实例化的 `game_service`, `image_service` 等
from . import game_session
from .handlers import game, listen, other, leaderboard
