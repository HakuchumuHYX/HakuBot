from __future__ import annotations

from pathlib import Path

from core.settings import ConfigModel, load_config
from utils.paths import PluginPaths

REGIONS = ("cn", "jp")


class RegionApi(ConfigModel):
    # 预测站地址只放 config.json
    events_url: str
    latest_url_template: str


class ApiConfig(ConfigModel):
    cn: RegionApi
    jp: RegionApi


class CacheConfig(ConfigModel):
    ttl_seconds: int
    clean_after_seconds: int


class PluginConfig(ConfigModel):
    api: ApiConfig
    cache: CacheConfig

    def get_api_config(self, region: str) -> RegionApi:
        normalized_region = region.strip().lower()
        if normalized_region not in REGIONS:
            raise ValueError(f"不支持的 region: {region}")
        return getattr(self.api, normalized_region)

    def get_events_url(self, region: str) -> str:
        return self.get_api_config(region).events_url

    def get_latest_url_template(self, region: str) -> str:
        return self.get_api_config(region).latest_url_template

    def get_cache_file(self, region: str) -> Path:
        normalized_region = region.strip().lower()
        if normalized_region not in REGIONS:
            raise ValueError(f"不支持的 region: {region}")
        return DATA_DIR / f"latest_{normalized_region}.png"


DATA_DIR = PluginPaths("sk_predict").data
DATA_DIR.mkdir(parents=True, exist_ok=True)

plugin_config = load_config("sk_predict", PluginConfig)
CACHE_TTL_SECONDS = plugin_config.cache.ttl_seconds
FILE_CLEAN_SECONDS = plugin_config.cache.clean_after_seconds
