# pjsk_guess_song/__init__.py

import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from nonebot import get_driver
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

# --- 1. 导入配置和服务 ---
from .config import plugin_config, data_dir
from .services.db_service import DBService
from .services.cache_service import CacheService
# [重构] 导入新的服务
from .services.audio_processor import AudioProcessor
from .services.image_service import ImageService
from .services.game_service import GameService


# --- 2. 插件元数据 ---
__plugin_meta__ = PluginMetadata(
    name="pjsk_guess_song",
    description="PJSK猜歌插件",
    usage="""
    🎵 基础指令
      `猜歌` - 普通
      `猜歌 1-7` - 对应特殊模式
    🎲 高级指令
      `随机猜歌` - 随机组合效果
      `猜歌手` - 竞猜演唱者
      `听<模式> [歌名/ID]` - 播放特殊音轨 (模式: 钢琴, 伴奏, 人声, 贝斯, 鼓组)
      `听anvo [歌名/ID] [角色名缩写]` - 播放指定或随机的 Another Vocal
    📊 其他功能
      `猜歌帮助` - 显示此帮助信息
    """,
    type="application",
    homepage="https://github.com/nichinichisou0609/astrbot_plugin_pjsk_guess_song",
    config=plugin_config.__class__,
)

# --- 3. 定义全局变量和初始化服务 ---
PLUGIN_VERSION = "1.1.3"
plugin_dir = Path(__file__).parent
resources_dir = plugin_dir / "resources"
output_dir = data_dir / "output"
output_dir.mkdir(parents=True, exist_ok=True)

db_path = data_dir / "guess_song_data.db"

# [重构] 实例化所有服务
db_service = DBService(str(db_path))
cache_service = CacheService(resources_dir, output_dir, plugin_config)

# [重构] 创建一个共享的线程池
executor = ThreadPoolExecutor(max_workers=5)

# [重构] 实例化新的子服务
audio_processor = AudioProcessor(cache_service, output_dir, executor)
image_service = ImageService(cache_service, resources_dir, output_dir, PLUGIN_VERSION, executor)

# [重构] 实例化游戏逻辑服务，并注入其依赖
game_service = GameService(cache_service, plugin_config, audio_processor, PLUGIN_VERSION)


# --- 4. 注册 Nonebot 启动/关闭 钩子 ---
driver = get_driver()

@driver.on_startup
async def _on_startup():
    """Nonebot 启动时执行异步初始化"""
    await db_service.init_db()
    await cache_service.load_resources_and_manifest()
    asyncio.create_task(cache_service.periodic_cleanup_task())
    logger.info("PJSK 猜歌插件服务已启动。")


@driver.on_shutdown
async def _on_shutdown():
    """Nonebot 关闭时执行清理"""
    # [重构] 关闭所有需要关闭的服务
    await audio_processor.terminate()
    await cache_service.terminate()
    executor.shutdown(wait=False)
    logger.info("PJSK 猜歌插件服务已终止。")


# --- 5. 导入处理器模块以注册 Matcher ---
# 导入时，它们会从本文件导入已实例化的 `game_service`, `image_service` 等
from . import game_session
from .handlers import game, listen, other, leaderboard