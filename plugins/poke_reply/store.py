"""投稿数据：每群文本（json）与图片（目录），文本转图片设置，删除申请，最近发出的投稿内容

投稿内容统一表示为 {"kind": "text", "text": ...} 或 {"kind": "image", "filenames": [...]}。
文本和图片每次读盘：手改文件立即生效，也没有内存副本能把文件覆盖掉。
"""

import json
import time
import uuid
from pathlib import Path

from nonebot import logger

from plugins.poke_reply.config import (
    DELETE_REQUESTS_FILE,
    IMAGE_FILES_DIR,
    PENDING_REQUEST_TTL,
    PROCESSED_REQUEST_TTL,
    RECENT_MESSAGE_TTL,
    TEXT_FILES_DIR,
    TEXT_TO_IMAGE_FILE,
)
from utils.json_io import atomic_write_bytes, atomic_write_json

# 群号 → 长文本转图片的字数阈值；不在表里即未开启
_text_to_image: dict[int, int] = {}
_requests: dict[str, dict] = {}
# (群号, 消息 ID) → 发出的投稿内容，供申请删除、转文字查找；重启即丢
_recent: dict[tuple[int, int], dict] = {}


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load() -> None:
    _text_to_image.clear()
    _text_to_image.update(
        {
            int(gid): threshold
            for gid, threshold in _read_json(TEXT_TO_IMAGE_FILE, {}).items()
        }
    )
    _requests.clear()
    _requests.update(_read_json(DELETE_REQUESTS_FILE, {}))
    _prune_requests()


# --- 投稿 ---


def _text_path(group_id: int) -> Path:
    return TEXT_FILES_DIR / f"text_{group_id}.json"


def image_dir(group_id: int) -> Path:
    return IMAGE_FILES_DIR / f"group_{group_id}"


def texts(group_id: int) -> list[str]:
    return _read_json(_text_path(group_id), [])


def images(group_id: int) -> list[Path]:
    """按投稿先后排序"""
    directory = image_dir(group_id)
    if not directory.exists():
        return []
    return sorted(directory.iterdir(), key=lambda path: path.stat().st_mtime)


def add_text(group_id: int, text: str) -> None:
    atomic_write_json(_text_path(group_id), texts(group_id) + [text])


def add_image(group_id: int, data: bytes, extension: str) -> str:
    filename = f"{uuid.uuid4().hex}.{extension}"
    atomic_write_bytes(image_dir(group_id) / filename, data)
    return filename


def remove_content(group_id: int, content: dict) -> bool:
    """删掉一条投稿内容；内容已经不在了返回 False"""
    if content["kind"] == "text":
        current = texts(group_id)
        if content["text"] not in current:
            return False
        current.remove(content["text"])
        atomic_write_json(_text_path(group_id), current)
        return True

    removed = True
    for filename in content["filenames"]:
        path = image_dir(group_id) / filename
        if path.exists():
            path.unlink()
        else:
            removed = False
    return removed


def group_ids() -> list[int]:
    ids = {
        int(p.stem.removeprefix("text_")) for p in TEXT_FILES_DIR.glob("text_*.json")
    }
    ids |= {int(p.name.removeprefix("group_")) for p in IMAGE_FILES_DIR.glob("group_*")}
    return sorted(ids)


# --- 文本转图片 ---


def text_to_image_threshold(group_id: int) -> int | None:
    return _text_to_image.get(group_id)


def set_text_to_image(group_id: int, threshold: int | None) -> None:
    """threshold 为 None 即关闭"""
    if threshold is None:
        _text_to_image.pop(group_id, None)
    else:
        _text_to_image[group_id] = threshold
    atomic_write_json(
        TEXT_TO_IMAGE_FILE, {str(gid): value for gid, value in _text_to_image.items()}
    )


# --- 删除申请 ---


def _save_requests() -> None:
    atomic_write_json(DELETE_REQUESTS_FILE, _requests)


def _prune_requests() -> None:
    now = time.time()
    expired = [
        request_id
        for request_id, request in _requests.items()
        if (
            now - request["request_time"] > PENDING_REQUEST_TTL
            if request["status"] == "pending"
            else now - request["process_time"] > PROCESSED_REQUEST_TTL
        )
    ]
    for request_id in expired:
        del _requests[request_id]
    if expired:
        logger.info(f"清理了 {len(expired)} 个过期的删除申请")
        _save_requests()


def add_request(
    group_id: int, message_id: int, requester_id: int, content: dict
) -> dict:
    _prune_requests()
    request_id = uuid.uuid4().hex[:8]
    _requests[request_id] = {
        "request_id": request_id,
        "group_id": group_id,
        "message_id": message_id,
        "requester_id": requester_id,
        "content": content,
        "status": "pending",
        "request_time": time.time(),
        "process_time": None,
        "processor_id": None,
        # 发给 SU 的通知消息，SU 直接回复通知即可审批
        "notice_ids": [],
    }
    _save_requests()
    return _requests[request_id]


def set_notice_ids(request_id: str, notice_ids: list[int]) -> None:
    _requests[request_id]["notice_ids"] = notice_ids
    _save_requests()


def get_request(request_id: str) -> dict | None:
    return _requests.get(request_id)


def request_by_notice(message_id: int) -> dict | None:
    return next((r for r in _requests.values() if message_id in r["notice_ids"]), None)


def pending_requests() -> list[dict]:
    return [r for r in _requests.values() if r["status"] == "pending"]


def finish_request(request_id: str, status: str, processor_id: int) -> None:
    _requests[request_id].update(
        status=status, process_time=time.time(), processor_id=processor_id
    )
    _save_requests()


def clear_processed_requests() -> int:
    processed = [rid for rid, r in _requests.items() if r["status"] != "pending"]
    for request_id in processed:
        del _requests[request_id]
    _save_requests()
    return len(processed)


def request_count() -> int:
    return len(_requests)


# --- 最近发出的投稿内容 ---


def remember(
    group_id: int, message_id: int, content: dict, rendered: bool = False
) -> None:
    """rendered：长文本转成图片发出的，可以转文字"""
    now = time.time()
    for key in [key for key, record in _recent.items() if record["expire"] < now]:
        del _recent[key]
    _recent[(group_id, message_id)] = {
        "content": content,
        "rendered": rendered,
        "expire": now + RECENT_MESSAGE_TTL,
    }


def recent(group_id: int, message_id: int) -> dict | None:
    record = _recent.get((group_id, message_id))
    if record is None or record["expire"] < time.time():
        return None
    return record


def forget(group_id: int, message_id: int) -> None:
    _recent.pop((group_id, message_id), None)
