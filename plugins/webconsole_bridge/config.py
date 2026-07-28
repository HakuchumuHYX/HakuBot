from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONFIG_FILE = Path(__file__).with_name("config.json")
_ALLOWED_KEYS = {
    "enabled",
    "database_path",
    "spool_path",
    "probe_interval_seconds",
}


def _read_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("enabled must be a JSON boolean")


def _read_probe_interval(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("probe_interval_seconds must be a number")
    try:
        interval = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "probe_interval_seconds must be a number"
        ) from exc
    if interval < 5:
        raise ValueError(
            "probe_interval_seconds must be at least 5 seconds"
        )
    return interval


def _read_absolute_path(raw: Any, name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{name} must be a non-empty absolute path")
    path = Path(raw.strip())
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    enabled: bool
    database_path: Path
    spool_path: Path
    probe_interval_seconds: float

    @classmethod
    def from_file(cls, path: Path = CONFIG_FILE) -> "BridgeConfig":
        if not path.is_file():
            raise ValueError(
                "config.json is missing; copy config.example.json and "
                "fill in the shared WebConsole storage paths"
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"cannot read config.json ({type(exc).__name__})"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError("config.json must contain a JSON object")
        unknown = sorted(set(raw) - _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"config.json contains unknown fields: {', '.join(unknown)}"
            )
        return cls(
            enabled=_read_bool(raw.get("enabled", True)),
            database_path=_read_absolute_path(
                raw.get("database_path"),
                "database_path",
            ),
            spool_path=_read_absolute_path(
                raw.get("spool_path"),
                "spool_path",
            ),
            probe_interval_seconds=_read_probe_interval(
                raw.get("probe_interval_seconds", 60)
            ),
        )

    @classmethod
    def disabled(cls) -> "BridgeConfig":
        return cls(
            enabled=False,
            database_path=Path("/nonexistent/webconsole.db"),
            spool_path=Path("/nonexistent/webconsole-spool"),
            probe_interval_seconds=60,
        )
