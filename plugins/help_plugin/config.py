from utils.paths import PluginPaths
from pydantic import BaseModel
import json

CONFIG_PATH = PluginPaths("help_plugin").config / "config.json"


class HelpConfig(BaseModel):
    # 回复给用户的整段文字，帮助站网址写在这里，不进代码
    text: str


def load_config() -> HelpConfig:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return HelpConfig.parse_obj(json.load(f))
