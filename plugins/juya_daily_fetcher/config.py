from __future__ import annotations
from utils.paths import PluginPaths

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from utils.logging import get_logger

logger = get_logger("juya_daily_fetcher.config")

PLUGIN_NAME = "juya_daily_fetcher"
PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE_PATH = PluginPaths("juya_daily_fetcher").config / "config.json"
DATA_DIR = PluginPaths("juya_daily_fetcher").data
STATE_FILE = DATA_DIR / "state.json"
ARTICLE_DIR = DATA_DIR / "articles"
TEMPLATE_DIR = PLUGIN_DIR / "templates"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LLMConfig(StrictModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: float = 120.0
    proxy: Optional[str] = None
    max_tokens: Optional[int] = 1024
    thinking_enabled: bool = False
    reasoning_effort: Optional[str] = None
    extra_body: dict = Field(default_factory=dict)


class Config(StrictModel):
    feed_url: str = "https://daily.juya.uk/rss.xml"
    targets: list[str] = Field(default_factory=list)
    debug_user_id: str = ""
    poll_interval_minutes: int = 15
    pending_max_age_minutes: int = 60
    pending_max_failures: int = 3
    article_retention_days: int = 7
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @field_validator("targets")
    @classmethod
    def _normalize_targets(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("debug_user_id", "feed_url")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return str(value or "").strip()

    def llm_ready(self) -> bool:
        return bool(
            self.llm.api_key.strip()
            and self.llm.base_url.strip()
            and self.llm.model.strip()
        )


def load_plugin_config() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE_PATH.exists():
        logger.info(f"未找到配置文件 {CONFIG_FILE_PATH}，使用内存默认配置")
        return Config()

    try:
        raw = json.loads(CONFIG_FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"读取 config.json 失败: {e}，将使用默认配置（不覆盖原文件）")
        return Config()
    if not isinstance(raw, dict):
        logger.error("config.json 顶层不是对象，将使用默认配置（不覆盖原文件）")
        return Config()
    try:
        config = Config.model_validate(raw)
    except ValueError as e:
        logger.error(f"校验 config.json 失败: {e}，将使用默认配置（不覆盖原文件）")
        return Config()
    return config


plugin_config = load_plugin_config()
