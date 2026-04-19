from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .tools import get_exc_desc, get_logger

logger = get_logger("moesekai_hub")

REMOTE_URL = "https://github.com/moe-sekai/MoeSekai-Hub.git"
BRANCH = "main"
SPARSE_PATHS = ("story/detail", "mangas")

REPO_DIR = Path("/opt/moesekai-hub")
INDEX_DIR = Path("/opt/moesekai-hub-index")
EVENT_INDEX_FILE = INDEX_DIR / "events.json"
MANGA_INDEX_FILE = INDEX_DIR / "mangas.json"
STATE_FILE = INDEX_DIR / "state.json"

SYNC_INTERVAL_HOURS = 6
INITIAL_SYNC_DELAY_SECONDS = 15

_sync_lock = asyncio.Lock()


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


async def _run_git(*args: str, cwd: Path | None = None) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr_text or stdout_text or f"exit code {proc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} 失败: {detail}")
    return stdout_text


async def _rev_parse(ref: str) -> str:
    return await _run_git("-C", str(REPO_DIR), "rev-parse", ref)


async def _ensure_repo_initialized() -> None:
    if (REPO_DIR / ".git").exists():
        await _run_git("-C", str(REPO_DIR), "sparse-checkout", "set", *SPARSE_PATHS)
        return

    if REPO_DIR.exists() and any(REPO_DIR.iterdir()):
        raise RuntimeError(f"{REPO_DIR} 已存在且不是可用的 Git 仓库，请先手动检查")

    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    await _run_git(
        "clone",
        "--branch",
        BRANCH,
        "--depth",
        "1",
        "--filter=blob:none",
        "--sparse",
        REMOTE_URL,
        str(REPO_DIR),
    )
    await _run_git("-C", str(REPO_DIR), "sparse-checkout", "set", *SPARSE_PATHS)


def _build_event_index() -> list[dict[str, Any]]:
    event_dir = REPO_DIR / "story" / "detail"
    items: list[dict[str, Any]] = []
    if not event_dir.exists():
        return items

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


def _find_local_manga_path(manga_dir: Path, manga_id: int) -> Path | None:
    for path in sorted(manga_dir.glob(f"{manga_id}.*")):
        if path.is_file() and path.name != "mangas.json":
            return path
    return None


def _build_manga_index() -> list[dict[str, Any]]:
    manga_dir = REPO_DIR / "mangas"
    meta_path = manga_dir / "mangas.json"
    items: list[dict[str, Any]] = []
    if not meta_path.exists():
        return items

    raw_data = _read_json_file(meta_path)
    if not isinstance(raw_data, dict):
        raise ValueError("mangas.json 格式不是对象")

    for key, value in raw_data.items():
        if not isinstance(value, dict):
            continue
        try:
            manga_id = int(value.get("id") or key)
            local_path = _find_local_manga_path(manga_dir, manga_id)
            items.append(
                {
                    "id": manga_id,
                    "title": value.get("title", ""),
                    "file_name": local_path.name if local_path else "",
                    "relative_path": local_path.relative_to(REPO_DIR).as_posix() if local_path else "",
                    "image_url": value.get("manga", ""),
                    "post_url": value.get("url", ""),
                    "published_at": value.get("date"),
                    "contributors": value.get("contributors", {}),
                    "updated_at": _file_mtime_iso(local_path) if local_path else "",
                }
            )
        except Exception:
            logger.exception(f"构建漫画索引失败: manga_id={key}")

    items.sort(key=lambda item: item["id"], reverse=True)
    return items


def _rebuild_indexes() -> dict[str, int]:
    events = _build_event_index()
    mangas = _build_manga_index()
    _atomic_write_json(EVENT_INDEX_FILE, events)
    _atomic_write_json(MANGA_INDEX_FILE, mangas)
    return {"event_count": len(events), "manga_count": len(mangas)}


def load_event_index() -> list[dict[str, Any]]:
    if not EVENT_INDEX_FILE.exists():
        raise FileNotFoundError(f"事件索引不存在: {EVENT_INDEX_FILE}")
    data = _read_json_file(EVENT_INDEX_FILE)
    if not isinstance(data, list):
        raise ValueError("事件索引格式错误")
    return data


def load_manga_index() -> list[dict[str, Any]]:
    if not MANGA_INDEX_FILE.exists():
        raise FileNotFoundError(f"漫画索引不存在: {MANGA_INDEX_FILE}")
    data = _read_json_file(MANGA_INDEX_FILE)
    if not isinstance(data, list):
        raise ValueError("漫画索引格式错误")
    return data


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    data = _read_json_file(STATE_FILE)
    return data if isinstance(data, dict) else {}


def get_event_detail_path(event_id: int) -> Path:
    return REPO_DIR / "story" / "detail" / f"event_{event_id:03d}.json"


async def ensure_event_detail_path(event_id: int, *, auto_sync: bool = True) -> Path | None:
    path = get_event_detail_path(event_id)
    if path.exists():
        return path
    if not auto_sync:
        return None

    await sync_repo_and_rebuild_index(reason=f"missing_event_{event_id}")
    return path if path.exists() else None


async def ensure_event_index_ready() -> None:
    if EVENT_INDEX_FILE.exists():
        return
    await sync_repo_and_rebuild_index(reason="missing_event_index")


async def ensure_manga_index_ready() -> None:
    if MANGA_INDEX_FILE.exists():
        return
    await sync_repo_and_rebuild_index(reason="missing_manga_index")


async def sync_repo_and_rebuild_index(*, force_rebuild: bool = False, reason: str = "manual") -> dict[str, Any]:
    async with _sync_lock:
        state = load_state()
        state["last_sync_at"] = _now_iso()
        state["last_reason"] = reason
        _atomic_write_json(STATE_FILE, state)

        try:
            await _ensure_repo_initialized()

            before_commit = await _rev_parse("HEAD")
            await _run_git("-C", str(REPO_DIR), "fetch", "origin", BRANCH, "--depth", "1")
            remote_commit = await _rev_parse(f"origin/{BRANCH}")
            updated = before_commit != remote_commit

            if updated:
                await _run_git("-C", str(REPO_DIR), "pull", "--ff-only", "origin", BRANCH)

            await _run_git("-C", str(REPO_DIR), "sparse-checkout", "reapply")

            need_rebuild = (
                force_rebuild
                or updated
                or not EVENT_INDEX_FILE.exists()
                or not MANGA_INDEX_FILE.exists()
            )

            counts = _rebuild_indexes() if need_rebuild else {
                "event_count": len(load_event_index()),
                "manga_count": len(load_manga_index()),
            }
            head_commit = await _rev_parse("HEAD")

            state.update(
                {
                    "last_success_at": _now_iso(),
                    "last_commit": head_commit,
                    "last_error": "",
                    "updated": updated,
                    **counts,
                }
            )
            _atomic_write_json(STATE_FILE, state)

            result = {"updated": updated, "commit": head_commit, **counts}
            logger.info(f"MoeSekai-Hub 同步完成: {result}")
            return result
        except Exception as e:
            state["last_error"] = get_exc_desc(e)
            _atomic_write_json(STATE_FILE, state)
            logger.exception(f"MoeSekai-Hub 同步失败: {e}")
            raise
