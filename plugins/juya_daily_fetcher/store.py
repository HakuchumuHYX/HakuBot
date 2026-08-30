from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils.json_io import atomic_write_json
from .config import STATE_FILE, plugin_config
from .parser import now_iso


def load_state() -> dict[str, Any]:
    path = STATE_FILE
    if not path.exists():
        return {}
    try:
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read state file {path}: {exc}") from exc


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, state, indent=2)


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value, indent=2)


def pending_expired(pending: dict[str, Any]) -> bool:
    created_at = str(pending.get("created_at") or "")
    fail_count = int(pending.get("fail_count") or 0)
    if fail_count >= max(1, plugin_config.pending_max_failures):
        return True
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age_seconds = (datetime.now(timezone.utc) - created).total_seconds()
    return age_seconds >= plugin_config.pending_max_age_minutes * 60


def mark_pending_failure(state: dict[str, Any], error: str) -> None:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return
    pending["fail_count"] = int(pending.get("fail_count") or 0) + 1
    pending["last_error"] = error
    pending["last_failed_at"] = now_iso()
    save_state(state)


def abandon_pending(state: dict[str, Any]) -> None:
    pending = state.get("pending")
    if isinstance(pending, dict) and isinstance(pending.get("seen_after"), dict):
        state["seen"] = pending["seen_after"]
    state["pending"] = None
    state["last_abandoned_at"] = now_iso()
    save_state(state)
