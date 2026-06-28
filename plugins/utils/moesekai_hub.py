from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .tools import get_exc_desc, get_logger

logger = get_logger("moesekai_hub")

REPO_DIR = Path("/opt/moesekai-hub")
INDEX_DIR = Path("/opt/moesekai-hub-index")
EVENT_INDEX_FILE = INDEX_DIR / "events.json"
STATE_FILE = INDEX_DIR / "state.json"

REBUILD_INTERVAL_HOURS = 6
INITIAL_REBUILD_DELAY_SECONDS = 15

_rebuild_lock = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _ensure_index_dir() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: Any) -> None:
    _ensure_index_dir()
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def _read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _event_dir() -> Path:
    return REPO_DIR / "story" / "detail"


def _read_head_commit() -> str:
    git_dir = REPO_DIR / ".git"
    head_path = git_dir / "HEAD"
    if not head_path.exists():
        return ""

    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = git_dir / head.removeprefix("ref: ").strip()
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.exists():
            ref_name = head.removeprefix("ref: ").strip()
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                commit, _, ref = line.partition(" ")
                if ref == ref_name:
                    return commit
        return ""
    return head


def _build_event_index() -> list[dict[str, Any]]:
    event_dir = _event_dir()
    if not event_dir.exists():
        raise FileNotFoundError(
            f"MoeSekai-Hub 本地剧情目录不存在: {event_dir}，请先运行外部同步脚本"
        )

    items: list[dict[str, Any]] = []
    for path in event_dir.glob("event_*.json"):
        try:
            data = _read_json_file(path)
            event_id = int(data.get("event_id") or path.stem.split("_", 1)[1])
            chapters = data.get("chapters") or []
            items.append(
                {
                    "event_id": event_id,
                    "file_name": path.name,
                    "relative_path": path.relative_to(REPO_DIR).as_posix(),
                    "title_jp": data.get("title_jp", ""),
                    "title_cn": data.get("title_cn", ""),
                    "chapter_count": len(chapters),
                    "summary_status": "已收录",
                    "updated_at": _file_mtime_iso(path),
                }
            )
        except Exception:
            logger.exception(f"构建活动索引失败: {path}")

    items.sort(key=lambda item: item["event_id"], reverse=True)
    return items


def load_event_index() -> list[dict[str, Any]]:
    if not EVENT_INDEX_FILE.exists():
        raise FileNotFoundError(f"事件索引不存在: {EVENT_INDEX_FILE}")
    data = _read_json_file(EVENT_INDEX_FILE)
    if not isinstance(data, list):
        raise ValueError("事件索引格式错误")
    return data


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    data = _read_json_file(STATE_FILE)
    return data if isinstance(data, dict) else {}


def rebuild_event_index(*, reason: str = "manual", commit: str = "") -> dict[str, Any]:
    state = load_state()
    state["last_rebuild_at"] = _now_iso()
    state["last_reason"] = reason
    _atomic_write_json(STATE_FILE, state)

    try:
        events = _build_event_index()
        _atomic_write_json(EVENT_INDEX_FILE, events)

        head_commit = commit or _read_head_commit()
        success_at = _now_iso()
        result = {
            "event_count": len(events),
            "commit": head_commit,
            "reason": reason,
            "last_success_at": success_at,
        }
        state.update(
            {
                "last_success_at": success_at,
                "last_commit": head_commit,
                "last_error": "",
                "event_count": len(events),
            }
        )
        _atomic_write_json(STATE_FILE, state)
        logger.info(f"MoeSekai-Hub 事件索引重建完成: {result}")
        return result
    except Exception as e:
        state["last_error"] = get_exc_desc(e)
        _atomic_write_json(STATE_FILE, state)
        logger.exception(f"MoeSekai-Hub 事件索引重建失败: {e}")
        raise


def get_event_detail_path(event_id: int) -> Path:
    return _event_dir() / f"event_{event_id:03d}.json"


async def ensure_event_detail_path(event_id: int, *, auto_rebuild: bool = True) -> Path | None:
    path = get_event_detail_path(event_id)
    if path.exists():
        return path
    if not auto_rebuild:
        return None

    async with _rebuild_lock:
        rebuild_event_index(reason=f"missing_event_{event_id}")
    return path if path.exists() else None


async def ensure_event_index_ready() -> None:
    if EVENT_INDEX_FILE.exists():
        return

    async with _rebuild_lock:
        if EVENT_INDEX_FILE.exists():
            return
        rebuild_event_index(reason="missing_event_index")


def main(argv: Sequence[str] | None = None) -> int:
    global REPO_DIR, INDEX_DIR, EVENT_INDEX_FILE, STATE_FILE

    parser = argparse.ArgumentParser(description="Maintain local MoeSekai-Hub indexes.")
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="rebuild /opt/moesekai-hub-index/events.json from local story/detail files",
    )
    parser.add_argument("--repo-dir", default="", help="override local MoeSekai-Hub repository path")
    parser.add_argument("--index-dir", default="", help="override local MoeSekai-Hub index directory")
    parser.add_argument("--reason", default="manual", help="reason written into state.json")
    parser.add_argument("--commit", default="", help="source repository commit written into state.json")
    args = parser.parse_args(argv)

    if not args.rebuild_index:
        parser.error("--rebuild-index is required")

    old_paths = (REPO_DIR, INDEX_DIR, EVENT_INDEX_FILE, STATE_FILE)
    try:
        if args.repo_dir:
            REPO_DIR = Path(args.repo_dir)
        if args.index_dir:
            INDEX_DIR = Path(args.index_dir)
        EVENT_INDEX_FILE = INDEX_DIR / "events.json"
        STATE_FILE = INDEX_DIR / "state.json"

        result = rebuild_event_index(reason=args.reason, commit=args.commit)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        REPO_DIR, INDEX_DIR, EVENT_INDEX_FILE, STATE_FILE = old_paths


if __name__ == "__main__":
    sys.exit(main())
