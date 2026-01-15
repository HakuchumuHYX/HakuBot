import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class PluginConfig(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-3.5-turbo"
    image_model: str = "dall-e-3"
    image_size: Optional[str] = None
    timeout: float = 60.0
    proxy: Optional[str] = None


CURRENT_PATH = Path(__file__).parent
CONFIG_PATH = CURRENT_PATH / "config.json"


def load_config() -> PluginConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件未找到: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return PluginConfig(**data)


plugin_config = load_config()
