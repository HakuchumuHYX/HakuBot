import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Extra
from nonebot.log import logger
import nonebot_plugin_localstore as localstore


class PluginConfig(BaseModel, extra=Extra.ignore):
    """猜卡面插件配置"""
    asset_base_url: str = "https://xxx/jp-assets/startapp/"
    masterdata_path: str = ""  # 留空则自动使用 haruki-sekai-master/master/
    guess_timeout: int = 60  # 猜测超时（秒）
    crop_rate_min: float = 0.15  # 裁剪最小比例
    crop_rate_max: float = 0.25  # 裁剪最大比例


PLUGIN_NAME = "pjsk_guess_card"
PLUGIN_DIR = Path(__file__).parent
data_dir = localstore.get_data_dir(PLUGIN_NAME)
data_dir.mkdir(parents=True, exist_ok=True)
CONFIG_FILE_PATH = PLUGIN_DIR / "config.json"

# 卡面图片本地缓存目录
CARD_IMAGES_DIR = data_dir / "card_images"
CARD_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def load_plugin_config() -> PluginConfig:
    """加载插件配置，不存在则创建默认配置"""
    if CONFIG_FILE_PATH.exists():
        logger.info(f"正在从 {CONFIG_FILE_PATH} 加载猜卡面插件配置...")
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


plugin_config = load_plugin_config()
