import json
from pathlib import Path
from typing import Any

import yaml

# 插件私有配置（只适用于 lunabot_imgexp）
BASE_DIR = Path(__file__).parent
CONFIG_JSON = BASE_DIR / "config.json"
CONFIG_YAML = BASE_DIR / "config.yaml"


class Config:
    def __init__(self):
        self.data = {}
        self.load()

    def load(self):
        if CONFIG_JSON.exists():
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        elif CONFIG_YAML.exists():
            with open(CONFIG_YAML, "r", encoding="utf-8") as f:
                self.data = yaml.safe_load(f) or {}
        else:
            self.data = {}
            # 创建默认配置 config.json
            with open(CONFIG_JSON, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "saucenao_apikey": "",
                        "serp_apikey": "",
                        "proxy": None,  # http://127.0.0.1:7890
                        "watermark_text": "",
                    },
                    f,
                    indent=4,
                    ensure_ascii=False,
                )

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
