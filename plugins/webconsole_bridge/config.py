from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field, StrictBool, field_validator

from core.settings import ConfigModel


class BridgeConfig(ConfigModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: StrictBool
    # 与 WebConsole 共用的存储位置，必须写绝对路径
    database_path: Path
    spool_path: Path
    probe_interval_seconds: float = Field(ge=5)

    @field_validator("database_path", "spool_path")
    @classmethod
    def _require_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("must be an absolute path")
        return value
