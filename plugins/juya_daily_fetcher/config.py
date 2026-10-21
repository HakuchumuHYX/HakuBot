from __future__ import annotations
from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator

PLUGIN_NAME = "juya_daily_fetcher"
PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PluginPaths("juya_daily_fetcher").data
STATE_FILE = DATA_DIR / "state.json"
ARTICLE_DIR = DATA_DIR / "articles"
TEMPLATE_DIR = PLUGIN_DIR / "templates"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class LLMConfig(ConfigModel):
    api_key: str
    base_url: str
    model: str
    timeout: float
    proxy: Optional[str]
    max_tokens: Optional[int]
    thinking_enabled: bool
    reasoning_effort: Optional[str]
    extra_body: dict


class Config(ConfigModel):
    feed_url: str
    targets: list[str]
    debug_user_id: str
    poll_interval_minutes: int = Field(ge=1)
    pending_max_age_minutes: int
    pending_max_failures: int
    article_retention_days: int = Field(ge=1)
    llm: LLMConfig

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


DATA_DIR.mkdir(parents=True, exist_ok=True)
ARTICLE_DIR.mkdir(parents=True, exist_ok=True)
plugin_config = load_config("juya_daily_fetcher", Config)
