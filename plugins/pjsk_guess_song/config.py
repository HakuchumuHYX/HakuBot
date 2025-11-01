# pjsk_guess_song/config.py

import json
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel, Extra
from nonebot.log import logger
import nonebot_plugin_localstore as localstore


# --- 1. 定义配置结构与默认值 ---

class PluginConfig(BaseModel, extra=Extra.ignore):
    """
    PJSK 猜歌插件配置模型
    (用于 config.json)
    """
    answer_timeout: int = 30
    daily_play_limit: int = 15
    super_users: List[str] = []
    group_whitelist: List[str] = []
    game_cooldown_seconds: int = 60
    max_guess_attempts: int = 10
    clip_duration_seconds: int = 10
    bonus_time_after_first_answer: int = 0
    end_game_after_bonus_time: bool = True
    debug_mode: bool = False
    daily_listen_limit: int = 5
    use_local_resources: bool = False
    remote_resource_url_base: str = "http://47.110.56.9"
    lightweight_mode: bool = False
    disable_guess_song_periods: List[Dict[str, str]] = [
        # 示例: {"start": "00:00", "end": "06:00", "message": "Zzz...这个时间点机器人需要休息..."}
    ]
    independent_daily_limit: bool = False
    random_mode_decay_factor: float = 0.75

    # --- [新功能] ---
    custom_footer_text: str = ""  # 在这里输入您的自定义水印文本

    # --- [新功能] 结束 ---

    class Config:
        validate_assignment = True


# --- 2. 定义配置文件路径 ---

PLUGIN_NAME = "pjsk_guess_song"
data_dir = localstore.get_data_dir(PLUGIN_NAME)
data_dir.mkdir(parents=True, exist_ok=True)
CONFIG_FILE_PATH = data_dir / "config.json"


# --- 3. 加载/创建配置的函数 ---

def load_plugin_config() -> PluginConfig:
    """
    加载插件配置。
    如果 config.json 不存在，则创建默认配置。
    """
    if CONFIG_FILE_PATH.exists():
        logger.info(f"正在从 {CONFIG_FILE_PATH} 加载 PJSK 猜歌插件配置...")
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                return PluginConfig.parse_obj(config_data)
        except Exception as e:
            logger.error(f"加载 config.json 失败: {e}，将使用默认配置。")
            return PluginConfig()
    else:
        logger.info(f"未找到 config.json，正在创建默认配置文件于 {CONFIG_FILE_PATH}")
        default_config = PluginConfig()
        try:
            with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(default_config.dict(), f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"创建默认配置文件失败: {e}")
        return default_config


# --- 4. 在导入时立即加载配置 ---
plugin_config = load_plugin_config()