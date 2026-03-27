from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PluginConfig:
    def __init__(self) -> None:
        self.plugin_dir = Path(__file__).parent
        self.config_file = self.plugin_dir / "config.json"
        self.data_dir = Path() / "data" / "sekai_cache"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        local_config = self._load_local_config()

        self.api_configs = {
            "cn": {
                "events_url": self._as_str(
                    self._deep_get(
                        local_config,
                        ("api", "cn", "events_url"),
                        self._deep_get(
                            local_config,
                            ("api", "events_url"),
                            "https://xxx.com/public/events?region=cn",
                        ),
                    ),
                    "https://xxx.com/public/events?region=cn",
                ),
                "latest_url_template": self._as_str(
                    self._deep_get(
                        local_config,
                        ("api", "cn", "latest_url_template"),
                        self._deep_get(
                            local_config,
                            ("api", "latest_url_template"),
                            "https://xxx.com/public/event/{event_id}/latest?region=cn",
                        ),
                    ),
                    "https://xxx.com/public/event/{event_id}/latest?region=cn",
                ),
            },
            "jp": {
                "events_url": self._as_str(
                    self._deep_get(
                        local_config,
                        ("api", "jp", "events_url"),
                        "https://xxx.com/public/events?region=jp",
                    ),
                    "https://xxx.com/public/events?region=jp",
                ),
                "latest_url_template": self._as_str(
                    self._deep_get(
                        local_config,
                        ("api", "jp", "latest_url_template"),
                        "https://xxx.com/public/event/{event_id}/latest?region=jp",
                    ),
                    "https://xxx.com/public/event/{event_id}/latest?region=jp",
                ),
            },
        }

        self.cache_ttl_seconds = self._as_int(
            self._deep_get(local_config, ("cache", "ttl_seconds"), 60),
            60,
        )
        self.file_clean_seconds = self._as_int(
            self._deep_get(local_config, ("cache", "clean_after_seconds"), 24 * 60 * 60),
            24 * 60 * 60,
        )

    def _load_local_config(self) -> dict[str, Any]:
        if not self.config_file.exists():
            return {}
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _deep_get(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
        current: Any = data
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return current

    @staticmethod
    def _as_str(value: Any, default: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def get_api_config(self, region: str) -> dict[str, str]:
        normalized_region = region.strip().lower()
        if normalized_region not in self.api_configs:
            raise ValueError(f"不支持的 region: {region}")
        return self.api_configs[normalized_region]

    def get_events_url(self, region: str) -> str:
        return self.get_api_config(region)["events_url"]

    def get_latest_url_template(self, region: str) -> str:
        return self.get_api_config(region)["latest_url_template"]

    def get_cache_file(self, region: str) -> Path:
        normalized_region = region.strip().lower()
        if normalized_region not in self.api_configs:
            raise ValueError(f"不支持的 region: {region}")
        return self.data_dir / f"latest_{normalized_region}.png"


plugin_config = PluginConfig()
DATA_DIR = plugin_config.data_dir
CACHE_TTL_SECONDS = plugin_config.cache_ttl_seconds
FILE_CLEAN_SECONDS = plugin_config.file_clean_seconds
