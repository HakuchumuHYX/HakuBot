from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths
from pathlib import Path


class PluginConfig(ConfigModel):
    """猜卡面插件配置"""

    asset_base_url: str  # 资源站地址只放 config.json
    masterdata_path: str  # 留空则自动使用 haruki-sekai-master/master/
    guess_timeout: int  # 猜测超时（秒）
    crop_rate_min: float  # 裁剪最小比例
    crop_rate_max: float  # 裁剪最大比例


PLUGIN_NAME = "pjsk_guess_card"
PLUGIN_DIR = Path(__file__).parent
data_dir = PluginPaths("pjsk_guess_card").data
data_dir.mkdir(parents=True, exist_ok=True)

plugin_config = load_config("pjsk_guess_card", PluginConfig)
