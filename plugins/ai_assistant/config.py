import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class PluginConfig(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-3.5-turbo"
    system_prompt: str = (
        "你好！我是HakuBot的AI助手。请用活泼、亲切且自然的语气回答用户的问题。"
        "避免过于生硬的机器回复。如果回答包含长文本，请注意分段和排版。"
    )
    image_model: str = "dall-e-3"
    image_size: Optional[str] = None
    timeout: float = 60.0
    proxy: Optional[str] = None

    # --- Web Search (manual command only) ---
    # Tavily: https://tavily.com/
    tavily_api_key: Optional[str] = None
    web_search_max_results: int = 5
    web_search_depth: str = "basic"


CURRENT_PATH = Path(__file__).parent
CONFIG_PATH = CURRENT_PATH / "config.json"


def load_config() -> PluginConfig:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"配置文件未找到: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return PluginConfig(**data)


def save_config(config: PluginConfig):
    """保存配置到文件"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        # 使用 dict() 以兼容 Pydantic V1/V2，确保中文不乱码
        json.dump(config.dict(), f, indent=4, ensure_ascii=False)


plugin_config = load_config()
