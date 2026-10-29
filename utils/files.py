from pathlib import Path
from nonebot import logger


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
