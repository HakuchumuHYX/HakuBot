from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths, sekai_master_dir


class PluginConfig(ConfigModel):
    """猜卡面插件配置"""

    asset_base_url: str  # 资源站地址只放 config.json
    guess_timeout: int  # 猜测超时（秒）
    crop_rate_min: float  # 裁剪区域占原图宽高的最小比例
    crop_rate_max: float  # 裁剪区域占原图宽高的最大比例


plugin_config = load_config("pjsk_guess_card", PluginConfig)

NICKNAMES_FILE = PluginPaths("pjsk_guess_card").data / "nicknames.json"
MASTER_DIR = sekai_master_dir() / "master"
