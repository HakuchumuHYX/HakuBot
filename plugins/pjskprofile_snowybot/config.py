import json
from pathlib import Path
from pydantic import BaseModel
from nonebot.log import logger

CONFIG_FILE_PATH = Path(__file__).parent / "config.json"


class ConfigModel(BaseModel):
    url: str
    token: str
    watermark: str


def load_config() -> ConfigModel:
    """加载本地 config.json 文件"""
    if not CONFIG_FILE_PATH.exists():
        logger.warning(f"未找到配置文件: {CONFIG_FILE_PATH}，插件功能将无法正常使用。")
        return ConfigModel(url="", token="", watermark="")

    try:
        with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ConfigModel(**data)
    except Exception as e:
        logger.error(f"加载 PJSK Profile 配置文件失败: {e}")
        return ConfigModel(url="", token="", watermark="")


plugin_config = load_config()
