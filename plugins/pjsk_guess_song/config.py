from datetime import time

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths, sekai_master_dir


class DisabledPeriod(ConfigModel):
    start: time
    end: time
    # 留空时用默认提示
    message: str = ""


class Settings(ConfigModel):
    """config/plugins/pjsk_guess_song/config.json，结构与文件一一对应"""

    answer_timeout: int
    daily_play_limit: int
    daily_listen_limit: int
    game_cooldown_seconds: int
    # 每人每局最多猜几次，0 为不限
    max_guess_attempts: int
    clip_duration_seconds: int
    # 有人答对后再等几秒就结束，0 为一直等到超时
    bonus_time_after_first_answer: int
    # 开启时游戏次数按“人 × 群”分开计，否则每人全局共用
    independent_daily_limit: bool
    disable_guess_song_periods: list[DisabledPeriod]
    custom_footer_text: str
    # 资源站地址只放 config.json
    asset_url_base: str
    asset_server: str


settings = load_config("pjsk_guess_song", Settings)

DATA_DIR = PluginPaths("pjsk_guess_song").data
DB_FILE = DATA_DIR / "guess_song_data.db"
# 别名手动维护：原来的别名接口已经下线
ALIASES_FILE = DATA_DIR / "song_aliases.json"
BACKGROUND_FILE = DATA_DIR / "ranking_bg.png"
MASTER_DIR = sekai_master_dir() / "master"
VERSION_FILE = sekai_master_dir() / "versions" / "current_version.json"
