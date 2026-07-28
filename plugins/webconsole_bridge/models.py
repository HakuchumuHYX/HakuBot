from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BridgeState(str, Enum):
    DISABLED = "disabled"
    DISABLED_UNAVAILABLE = "disabled_unavailable"
    AVAILABLE = "available"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    available: bool
    reason: str = ""

    @classmethod
    def success(cls) -> "ProbeResult":
        return cls(available=True)

    @classmethod
    def failure(cls, reason: str) -> "ProbeResult":
        return cls(available=False, reason=reason)

