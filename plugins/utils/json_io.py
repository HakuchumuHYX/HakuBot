"""
共享 JSON 原子写工具。

很多插件的 save_xxx 直接 open('w') 覆盖写，进程崩溃/断电时会留下截断的 JSON 文件。
统一改用「临时文件 + fsync + os.replace」的原子替换方式，保证目标文件要么是旧内容、
要么是完整的新内容。
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Union


def atomic_write_json(
    path: Union[str, Path],
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """原子写 JSON 文件。

    先写同目录下的临时文件并 fsync，再用 os.replace 原子替换目标文件，
    避免写入中途崩溃导致目标文件被截断。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
