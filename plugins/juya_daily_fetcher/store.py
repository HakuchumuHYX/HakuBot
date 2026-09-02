from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils.json_io import atomic_write_json
from ..utils.tools import get_logger
from .config import STATE_FILE, plugin_config
from .parser import now_iso

logger = get_logger("juya_daily_fetcher.store")

_STALE_STATE_KEYS = ("migrated_from",)


def load_state() -> dict[str, Any]:
    path = STATE_FILE
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error(f"读取状态文件失败，本次以空状态运行（原文件保留待人工检查）: {exc}")
        return {}
    if not isinstance(value, dict):
        logger.error("状态文件顶层不是对象，本次以空状态运行")
        return {}
    for key in _STALE_STATE_KEYS:
        value.pop(key, None)
    return value


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE_FILE, state, indent=2)


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, value, indent=2)


def pending_expired(pending: dict[str, Any]) -> bool:
    fail_count = int(pending.get("fail_count") or 0)
    if fail_count >= max(1, plugin_config.pending_max_failures):
        return True
    created_at = str(pending.get("created_at") or "")
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age_seconds = (datetime.now(timezone.utc) - created).total_seconds()
    return age_seconds >= plugin_config.pending_max_age_minutes * 60


def pending_images_missing(pending: dict[str, Any]) -> bool:
    raw_paths = pending.get("image_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        return True
    paths = [Path(str(item)) for item in raw_paths if str(item).strip()]
    if not paths:
        return True
    return any(not path.is_file() for path in paths)


def pending_keep_names(pending: dict[str, Any] | None) -> set[str]:
    if not isinstance(pending, dict):
        return set()
    names: set[str] = set()
    json_path = str(pending.get("json_path") or "").strip()
    if json_path:
        names.add(Path(json_path).name)
    raw_paths = pending.get("image_paths")
    if isinstance(raw_paths, list):
        for item in raw_paths:
            text = str(item).strip()
            if text:
                names.add(Path(text).name)
    return names


def mark_pending_failure(state: dict[str, Any], error: str) -> None:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return
    pending["fail_count"] = int(pending.get("fail_count") or 0) + 1
    pending["last_error"] = error
    pending["last_failed_at"] = now_iso()
    save_state(state)


def mark_pending_alerted(state: dict[str, Any]) -> None:
    pending = state.get("pending")
    if not isinstance(pending, dict):
        return
    pending["alerted_at"] = now_iso()
    save_state(state)


def abandon_pending(state: dict[str, Any]) -> None:
    """丢弃 pending，但不推进 seen，以便下一轮重新渲染入队。"""
    state["pending"] = None
    state["last_abandoned_at"] = now_iso()
    save_state(state)
