import copy
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from nonebot import logger

T = TypeVar("T")


@dataclass
class JsonLoadResult:
    success: bool
    data: Any
    error: Optional[Exception] = None
    backup_path: Optional[Path] = None


def _default_copy(default: T) -> T:
    return copy.deepcopy(default)


def backup_corrupt_file(path: Path, suffix: str = "corrupt") -> Optional[Path]:
    try:
        if not path.exists():
            return None
        backup_path = path.with_name(
            f"{path.name}.{suffix}.{int(time.time())}.{uuid.uuid4().hex[:8]}"
        )
        shutil.copy2(path, backup_path)
        logger.warning(f"已备份异常 JSON 文件: {path} -> {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"备份异常 JSON 文件失败 {path}: {e}")
        return None


def load_json_file(
    path: Path,
    expected_type: Type[T],
    default: T,
    *,
    backup_on_error: bool = True,
) -> JsonLoadResult:
    try:
        if not path.exists():
            return JsonLoadResult(True, _default_copy(default))

        raw_content = path.read_text(encoding="utf-8")
        if not raw_content.strip():
            error = ValueError("JSON 文件为空")
            backup_path = backup_corrupt_file(path) if backup_on_error else None
            return JsonLoadResult(False, _default_copy(default), error, backup_path)

        data = json.loads(raw_content)
        if not isinstance(data, expected_type):
            error = TypeError(f"JSON 类型错误: expected {expected_type.__name__}, got {type(data).__name__}")
            backup_path = backup_corrupt_file(path) if backup_on_error else None
            return JsonLoadResult(False, _default_copy(default), error, backup_path)
        return JsonLoadResult(True, data)
    except Exception as e:
        backup_path = backup_corrupt_file(path) if backup_on_error else None
        return JsonLoadResult(False, _default_copy(default), e, backup_path)


def atomic_write_json(path: Path, data: Any, expected_type: Type, *, indent: int = 2) -> bool:
    if not isinstance(data, expected_type):
        logger.error(
            f"拒绝写入 JSON 文件 {path}: expected {expected_type.__name__}, got {type(data).__name__}"
        )
        return False

    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())

        with open(tmp_path, "r", encoding="utf-8") as f:
            written_data = json.load(f)
        if not isinstance(written_data, expected_type):
            raise TypeError(
                f"写入后的 JSON 类型错误: expected {expected_type.__name__}, got {type(written_data).__name__}"
            )

        os.replace(tmp_path, path)
        _fsync_parent_dir(path)
        return True
    except Exception as e:
        logger.error(f"原子写入 JSON 文件失败 {path}: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception as cleanup_error:
            logger.error(f"清理临时 JSON 文件失败 {tmp_path}: {cleanup_error}")
        return False


def _fsync_parent_dir(path: Path) -> None:
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        pass
