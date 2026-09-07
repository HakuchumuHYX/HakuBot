import asyncio
import os
import tempfile
from pathlib import Path
from datetime import timedelta
from typing import Union
from nonebot.log import logger

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
                        except:
                            pass
        return False


from urllib.parse import urlparse


async def safe_delete_file(file_path: str | Path | None, max_retries: int = 3) -> bool:
    if not file_path:
        return True

    path = Path(file_path)
    for i in range(max_retries):
        try:
            if path.exists():
                path.unlink()
            return True
        except PermissionError as e:
            if i < max_retries - 1:
                await asyncio.sleep(0.1)
            else:
                logger.warning(f"删除文件失败: {path}: {e}")
                return False
        except Exception as e:
            logger.warning(f"删除文件失败: {path}: {e}")
            return False
    return False


async def cleanup_files(*paths: str | Path | None) -> None:
    for path in paths:
        await safe_delete_file(path)


def ensure_output_dir(name: str) -> Path:
    output_dir = Path(tempfile.gettempdir()) / name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def guess_ext_from_url(url: str, default: str = "tmp") -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix and len(suffix) <= 8:
        return suffix
    return default.lstrip(".") or "tmp"
