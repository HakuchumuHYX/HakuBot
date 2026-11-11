from pathlib import Path

from pydantic import Field

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

PLUGIN_ID = "cs_stats"
QUERY_ID = "cs_stats:query"
ROAST_ID = "cs_stats:roast"

DATA_DIR = PluginPaths(PLUGIN_ID).data
TEMPLATE_DIR = Path(__file__).parent / "templates"


class LLMConfig(ConfigModel):
    api_key: str
    base_url: str
    model: str
    timeout: float
    proxy: str | None
    max_tokens: int | None
    thinking_enabled: bool
    reasoning_effort: str | None
    extra_body: dict


class Config(ConfigModel):
    # 完美的战绩接口都要带登录 token；重新登录用 scripts/pw_login.py，写入后发「完美重载」
    pw_token: str
    pw_steam_id: str
    pw_appversion: str
    # 5E arena 接口偶尔十几秒才返回
    http_timeout: float = Field(gt=0)
    max_accounts_per_platform: int = Field(ge=1)
    # 完美天梯本赛季没打时，最多往前找几个赛季
    pvp_season_lookback: int = Field(ge=0)
    llm: LLMConfig
    watermark_text: str


plugin_config = load_config(PLUGIN_ID, Config)
