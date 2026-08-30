from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger

logger = get_logger("juya_daily_fetcher.config")

PLUGIN_NAME = "juya_daily_fetcher"
PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE_PATH = PLUGIN_DIR / "config.json"
DATA_DIR = Path("data/juya_daily_fetcher")
STATE_FILE = DATA_DIR / "state.json"
ARTICLE_DIR = DATA_DIR / "articles"
TEMPLATE_DIR = PLUGIN_DIR / "templates"


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
    targets: list[str] = Field(
        default_factory=lambda: []
    )
    debug_user_id: str
    poll_interval_minutes: int = 15
    pending_max_age_minutes: int = 60
    pending_max_failures: int = 3
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @field_validator("targets")
    @classmethod
    def _normalize_targets(cls, value: list[str]) -> list[str]:
        targets = [str(item).strip() for item in value if str(item).strip()]
        if not targets:
            raise ValueError("targets 不能为空")
        return targets

    @field_validator("debug_user_id")
    @classmethod
    def _normalize_debug_user(cls, value: str) -> str:
        user_id = str(value or "").strip()
        if not user_id:
            raise ValueError("debug_user_id 不能为空")
        return user_id

    def llm_ready(self) -> bool:
        return bool(self.llm.api_key.strip() and self.llm.base_url.strip() and self.llm.model.strip())


def load_plugin_config() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE_PATH.exists():
        logger.info(f"未找到 config.json，正在创建默认配置文件于 {CONFIG_FILE_PATH}")
        default_config = Config()
        try:
            atomic_write_json(CONFIG_FILE_PATH, default_config.model_dump(), indent=4)
        except OSError as e:
            logger.error(f"创建默认配置文件失败: {e}，本次使用内存中的默认配置")
        return default_config

    try:
        raw = json.loads(CONFIG_FILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"读取 config.json 失败: {e}，将使用默认配置")
        return Config()
    if not isinstance(raw, dict):
        logger.error("config.json 顶层不是对象，将使用默认配置")
        return Config()
    try:
        config = Config.model_validate(raw)
    except ValueError as e:
        logger.error(f"校验 config.json 失败: {e}，将使用默认配置")
        return Config()
    _sync_config_file(raw, config)
    return config


def _sync_config_file(raw: dict, config: Config) -> None:
    dumped = config.model_dump()
    if _nested_keys(raw) == _nested_keys(dumped):
        return
    try:
        atomic_write_json(CONFIG_FILE_PATH, dumped, indent=4)
    except OSError as e:
        logger.warning(f"同步 config.json 失败: {e}")
        return
    logger.info("config.json 已按当前字段补齐缺失项")


def _nested_keys(value: object) -> object:
    if isinstance(value, dict):
        return {key: _nested_keys(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_nested_keys(item) for item in value]
    return True


plugin_config = load_plugin_config()
