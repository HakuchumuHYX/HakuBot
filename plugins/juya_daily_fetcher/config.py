from typing import Optional

from pydantic import Field

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

PLUGIN_ID = "juya_daily_fetcher"
STATE_FILE = PluginPaths(PLUGIN_ID).data / "state.json"


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
    debug_user_id: str
    poll_interval_minutes: int = Field(ge=1)
    llm: LLMConfig

    def llm_ready(self) -> bool:
        return bool(
            self.llm.api_key.strip()
            and self.llm.base_url.strip()
            and self.llm.model.strip()
        )


plugin_config = load_config(PLUGIN_ID, Config)
