import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from nonebot import get_driver
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from plugins.pjsk_guess_song.config import plugin_config, data_dir
from plugins.pjsk_guess_song.services.db_service import DBService
from plugins.pjsk_guess_song.services.cache_service import CacheService
from plugins.pjsk_guess_song.services.audio_processor import AudioProcessor
from plugins.pjsk_guess_song.services.image_service import ImageService
from plugins.pjsk_guess_song.services.game_service import GameService
from plugins.pjsk_guess_song.tools.generate_guess_song import (
    generate as generate_guess_song,
)

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

image_service = ImageService(
    cache_service, resources_dir, output_dir, PLUGIN_VERSION, executor, plugin_config
)

game_service = GameService(
    cache_service, plugin_config, audio_processor, PLUGIN_VERSION
)
