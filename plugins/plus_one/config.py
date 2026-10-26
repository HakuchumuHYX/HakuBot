from core.settings import ConfigModel, load_config, update_fields
from utils.paths import PluginPaths
from nonebot import logger

CONFIG_FILE = PluginPaths("plus_one").config / "config.json"


class Config(ConfigModel):
    blocked_words: set[str]


config = load_config("plus_one", Config)


def save_config() -> bool:
    """把命令改过的屏蔽词写回配置文件"""
    try:
        update_fields(
            CONFIG_FILE,
            {"blocked_words": sorted(config.blocked_words)},
        )
    except Exception as e:
        logger.exception(f"保存配置文件失败: {e}")
        return False
    logger.info("成功保存复读姬插件配置")
    return True
