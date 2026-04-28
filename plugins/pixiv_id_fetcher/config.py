from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


BASE_DIR = Path(__file__).parent
CONFIG_JSON = BASE_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "refresh_token": "",
    "client_id": "MOBrBDS8blbauoSck0ZfDbtuzpyT",
    "client_secret": "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj",
    "proxy": None,
    "timeout": 20.0,
    "allow_r18": False,
    "allow_r18g": False,
    "send_original": False,
    "max_pages": 9,
    "max_bytes": 20 * 1024 * 1024,
    "ugoira_zip_max_bytes": 30 * 1024 * 1024,
    "ugoira_max_frames": 150,
    "concurrency": 2,
    "reverse_proxy_domain": None,
}


class Config:
    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if CONFIG_JSON.exists():
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.data = {**DEFAULT_CONFIG, **loaded}
            return

        self.data = DEFAULT_CONFIG.copy()
        with open(CONFIG_JSON, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def refresh_token(self) -> str:
        return str(self.get("refresh_token", "") or "")

    @property
    def proxy(self) -> Optional[str]:
        value = self.get("proxy")
        return str(value) if value else None


config = Config()
