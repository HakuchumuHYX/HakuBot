"""
共享 JSON 原子写工具。

很多插件的 save_xxx 直接 open('w') 覆盖写，进程崩溃/断电时会留下截断的 JSON 文件。
统一改用「临时文件 + fsync + os.replace」的原子替换方式，保证目标文件要么是旧内容、
要么是完整的新内容。
"""

import json
import os
import tempfile
import copy
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union


@dataclass(frozen=True)
class JsonLoadResult:
    success: bool
    data: Any
    error: Exception | None = None
    backup_path: Path | None = None
    missing: bool = False


def load_json(
    path, *, expected_type=dict, default=None, missing_ok=False, backup_on_error=False
):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, expected_type):
            raise TypeError(
                f"Expected {expected_type.__name__}, got {type(data).__name__}"
            )
        return JsonLoadResult(True, data)
    except FileNotFoundError as exc:
        return JsonLoadResult(
            missing_ok,
            copy.deepcopy(default),
            None if missing_ok else exc,
            missing=True,
        )
    except (OSError, ValueError, TypeError) as exc:
        backup = None
        if backup_on_error and path.is_file():
            try:
                backup = path.with_name(f"{path.name}.corrupt.{time.time_ns()}")
                shutil.copy2(path, backup)
            except OSError:
                backup = None
        return JsonLoadResult(False, copy.deepcopy(default), exc, backup)


def atomic_write_bytes(path, data: bytes):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_write_json(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    expected_type: type | None = None,
) -> None:
    """原子写 JSON 文件。

    先写同目录下的临时文件并 fsync，再用 os.replace 原子替换目标文件，
    避免写入中途崩溃导致目标文件被截断。
    """
    if expected_type is not None and not isinstance(data, expected_type):
        raise TypeError(f"Expected {expected_type.__name__}")
    atomic_write_bytes(
        path, json.dumps(data, ensure_ascii=ensure_ascii, indent=indent).encode("utf-8")
    )
