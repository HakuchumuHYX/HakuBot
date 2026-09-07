from utils.paths import PluginPaths
import json
from typing import Any


# 插件私有配置（只适用于 lunabot_imgexp）
CONFIG_JSON = PluginPaths("lunabot_imgexp").config / "config.json"


class Config:
    def __init__(self):
        self.data = {}
        self.load()

    def load(self):
        if CONFIG_JSON.exists():
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self.data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value


config = Config()
