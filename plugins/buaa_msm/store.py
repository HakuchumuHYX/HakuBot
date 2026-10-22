"""持久状态：绑定、用户数据包与结果图、来访记录、处理中标记"""

import os
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from nonebot import logger

from utils.json_io import atomic_write_json, load_json
from plugins.buaa_msm.config import (
    BIND_FILE,
    REFRESH_HOURS,
    STORAGE_DIR,
    VISIT_HISTORY_FILE,
)

# 结果图落盘的文件名，网页从这里读图
RESULT_FILES = {"summary": "msr_summary.png", "map": "msr_map.png"}


def _load(path: Path, label: str) -> dict:
    # 文件损坏时先留一份备份，免得下次保存把它覆盖掉
    result = load_json(path, default={}, missing_ok=True, backup_on_error=True)
    if not result.success:
        logger.error(f"加载{label}失败，已按空数据处理: {result.error}")
    return result.data


# ============== 绑定 ==============

# {qq: 绑定内容}；绑定内容只用来给数据包命名，方便 bot 主认人
_binds: dict[str, str] = _load(BIND_FILE, "绑定数据")


def get_bind(qq: str) -> str | None:
    return _binds.get(qq)


def all_binds() -> dict[str, str]:
    return dict(_binds)


def set_bind(qq: str, content: str) -> None:
    atomic_write_json(BIND_FILE, {**_binds, qq: content})
    _binds[qq] = content


def delete_bind(qq: str) -> str | None:
    """返回被删掉的绑定内容；本来就没绑定时返回 None"""
    if qq not in _binds:
        return None
    atomic_write_json(BIND_FILE, {k: v for k, v in _binds.items() if k != qq})
    return _binds.pop(qq)


# ============== 数据包与结果图 ==============


def user_dir(qq: str) -> Path:
    return STORAGE_DIR / qq


def result_path(qq: str, kind: str) -> Path:
    return user_dir(qq) / RESULT_FILES[kind]


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")
    return name[:200] or "unnamed"


def reserve_upload_path(qq: str, original_name: str) -> Path:
    """这次上传要写入的路径。

    已绑定：{qq}_{绑定内容}_{时间}.bin；未绑定：沿用原文件名。重名时加 _1、_2 后缀。
    """
    bind = _binds.get(qq)
    if bind:
        name = f"{qq}_{_safe_filename(bind)}_{datetime.now():%Y%m%d_%H%M%S}.bin"
    else:
        name = _safe_filename(original_name)
    directory = user_dir(qq)
    directory.mkdir(parents=True, exist_ok=True)
    stem, suffix = os.path.splitext(name)
    path = directory / name
    counter = 1
    while path.exists():
        path = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return path


def latest_upload(qq: str) -> Path | None:
    directory = user_dir(qq)
    if not directory.is_dir():
        return None
    uploads = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.name not in RESULT_FILES.values()
    ]
    return max(uploads, key=lambda path: path.stat().st_mtime, default=None)


def drop_previous(qq: str, keep: Path) -> None:
    """删掉用户目录里除 keep 以外的文件：旧数据包和属于旧数据的结果图"""
    for path in user_dir(qq).iterdir():
        if path != keep:
            path.unlink()
            logger.debug(f"删除用户 {qq} 的旧文件: {path.name}")


def list_uploads() -> list[Path]:
    if not STORAGE_DIR.exists():
        return []
    return sorted(
        path
        for path in STORAGE_DIR.glob("*/*")
        if path.is_file() and path.name not in RESULT_FILES.values()
    )


def storage_size() -> int:
    if not STORAGE_DIR.exists():
        return 0
    return sum(path.stat().st_size for path in STORAGE_DIR.rglob("*") if path.is_file())


def clear_storage() -> int:
    """清空存储目录，返回删掉的用户目录数"""
    if not STORAGE_DIR.exists():
        return 0
    count = 0
    for path in STORAGE_DIR.iterdir():
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            count += 1
        except OSError as exc:
            logger.warning(f"删除失败 {path}: {exc}")
    logger.info(f"清理完成，删除了 {count} 个文件/目录")
    return count


# ============== 来访记录 ==============

# {qq: [{"period_key": "2026-10-22_morning", "timestamp": ..., "characters": [groupId, ...]}]}
_visits: dict[str, list[dict]] = _load(VISIT_HISTORY_FILE, "来访记录")


def _period_key() -> str:
    """5 点前算前一天下午时段，5～17 点算上午时段，之后算下午时段"""
    now = datetime.now()
    morning, afternoon = REFRESH_HOURS
    if now.hour < morning:
        return f"{now - timedelta(days=1):%Y-%m-%d}_afternoon"
    part = "morning" if now.hour < afternoon else "afternoon"
    return f"{now:%Y-%m-%d}_{part}"


def record_visits(qq: str, group_ids: list[str]) -> None:
    """记录这次上传的来访角色；同一时段重复上传时覆盖"""
    period = _period_key()
    records = [r for r in _visits.get(qq, []) if r["period_key"] != period]
    records.append(
        {"period_key": period, "timestamp": time.time(), "characters": group_ids}
    )
    _visits[qq] = records
    atomic_write_json(VISIT_HISTORY_FILE, _visits)


def repeated_visits(qq: str) -> set[str]:
    """本时段来访的角色里，今天其他时段也来过的"""
    period = _period_key()
    current: set[str] = set()
    previous: set[str] = set()
    for record in _visits.get(qq, []):
        target = current if record["period_key"] == period else previous
        target.update(record["characters"])
    return current & previous


def clear_visits() -> None:
    _visits.clear()
    atomic_write_json(VISIT_HISTORY_FILE, _visits)
    logger.debug("已清空角色访问历史")


# ============== 处理中标记 ==============

_busy: set[str] = set()


@contextmanager
def exclusive(qq: str):
    """同一用户同时只跑一份入库或生成；yield 是否抢到"""
    if qq in _busy:
        yield False
        return
    _busy.add(qq)
    try:
        yield True
    finally:
        _busy.discard(qq)
