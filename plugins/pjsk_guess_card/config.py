from utils.paths import PluginPaths
import json
from pathlib import Path
from pydantic import BaseModel
from nonebot.log import logger


class PluginConfig(BaseModel, extra="ignore"):
    """猜卡面插件配置"""

    asset_base_url: str = "https://xxx/jp-assets/startapp/"
    masterdata_path: str = ""  # 留空则自动使用 haruki-sekai-master/master/
    guess_timeout: int = 60  # 猜测超时（秒）
    crop_rate_min: float = 0.15  # 裁剪最小比例
    crop_rate_max: float = 0.25  # 裁剪最大比例


PLUGIN_NAME = "pjsk_guess_card"
PLUGIN_DIR = Path(__file__).parent
data_dir = PluginPaths("pjsk_guess_card").data
data_dir.mkdir(parents=True, exist_ok=True)
CONFIG_FILE_PATH = PluginPaths("pjsk_guess_card").config / "config.json"


def load_plugin_config() -> PluginConfig:
    """加载插件配置，文件不存在时使用内存默认值。"""
    if CONFIG_FILE_PATH.exists():
        logger.info(f"正在从 {CONFIG_FILE_PATH} 加载猜卡面插件配置...")
        try:
            with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                return PluginConfig.model_validate(config_data)
        except Exception as e:
            logger.error(f"加载 config.json 失败: {e}，将使用默认配置。")
            return PluginConfig()
    else:
        logger.info(f"未找到配置文件 {CONFIG_FILE_PATH}，使用内存默认配置")
        return PluginConfig()


plugin_config = load_plugin_config()
