# pjsk_guess_song/config.py
from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

from typing import List, Dict
from pydantic import ConfigDict


# --- 1. 定义配置结构与默认值 ---


class PluginConfig(ConfigModel):
    """
    PJSK 猜歌插件配置模型
    (用于 config.json)
    """

    answer_timeout: int
    daily_play_limit: int
    game_cooldown_seconds: int
    max_guess_attempts: int
    clip_duration_seconds: int
    bonus_time_after_first_answer: int
    end_game_after_bonus_time: bool
    debug_mode: bool
    daily_listen_limit: int
    use_local_resources: bool
    remote_resource_url_base: str
    lightweight_mode: bool
    disable_guess_song_periods: List[Dict[str, str]]
    independent_daily_limit: bool
    random_mode_decay_factor: float
    custom_footer_text: str
    full_mode: bool
    asset_url_base: str
    asset_server: str

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- 2. 定义配置文件路径 ---

PLUGIN_NAME = "pjsk_guess_song"
data_dir = PluginPaths("pjsk_guess_song").data
data_dir.mkdir(parents=True, exist_ok=True)


plugin_config = load_config("pjsk_guess_song", PluginConfig)
