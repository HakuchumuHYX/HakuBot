import asyncio
import os
import tempfile
from pathlib import Path
from datetime import timedelta
from typing import Union
from nonebot import logger

_delayed_tasks: set[asyncio.Task] = set()


async def close_temporary_files():
    tasks = tuple(_delayed_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class TemporaryPath:
    """
    临时文件路径管理器，支持 with 语句自动清理
    """

    def __init__(
        self, ext: str = None, remove_after: Union[bool, int, float, timedelta] = True
    ):
        self.ext = ext or "tmp"
        self.remove_after = remove_after
        self.path: Path = None

    def __enter__(self) -> Path:
        fd, path = tempfile.mkstemp(suffix=f".{self.ext}")
        os.close(fd)
        self.path = Path(path)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.path and self.path.exists():
            if self.remove_after is True:
                try:
                    os.remove(self.path)
                except Exception as e:
                    logger.warning(f"删除临时文件 {self.path} 失败: {e}")
            elif self.remove_after:
                # 延迟删除
                delay = 0
                if isinstance(self.remove_after, (int, float)):
                    delay = self.remove_after
                elif isinstance(self.remove_after, timedelta):
                    delay = self.remove_after.total_seconds()

                if delay > 0:

                    async def delayed_remove(p: Path, d: float):
                        try:
                            await asyncio.sleep(d)
                        finally:
                            try:
                                p.unlink(missing_ok=True)
                            except OSError as error:
                                logger.warning(
                                    f"Temporary file cleanup failed: {error}"
                                )

                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(delayed_remove(self.path, delay))
                        _delayed_tasks.add(task)
                        task.add_done_callback(_delayed_tasks.discard)
                    except RuntimeError:
                        # 如果没有运行的 loop，直接删除
                        try:
                            os.remove(self.path)
                        except OSError as e:
                            logger.warning(f"删除临时文件 {self.path} 失败: {e}")
        return False


def safe_delete_file(file_path: str | Path | None) -> bool:
    if not file_path:
        return True

    path = Path(file_path)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        logger.warning(f"删除文件失败: {path}: {e}")
        return False


def ensure_output_dir(name: str) -> Path:
    output_dir = Path(tempfile.gettempdir()) / name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir
