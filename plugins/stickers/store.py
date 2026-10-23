"""表情包库：list.json 记文件夹和别名，每个文件夹一个目录，图片按全库唯一的数字编号命名

HakuWeb 下载站直接读 list.json 和这些目录，格式和布局都不要改。
"""

import json
import random
import time
import uuid
from pathlib import Path

from nonebot import logger

from utils.json_io import atomic_write_bytes, atomic_write_json
from utils.paths import PluginPaths

LIBRARY = PluginPaths("stickers").data
LIST_FILE = LIBRARY / "list.json"
BACKUP_DIR = LIBRARY / "backup_duplicates"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
# 「随机表情」这类说法表示从全库抽，也不能拿来当文件夹名
ALL_KEYWORDS = {"stickers", "sticker", "表情", "表情包"}

# 文件夹 → 别名，保持 list.json 的顺序：重新编号按这个顺序排
folders: dict[str, list[str]] = {}
# 别名 → 文件夹
aliases: dict[str, str] = {}
_max_id = 0


def load() -> None:
    """读 list.json；读不到或格式不对直接抛，内存里的旧数据不动"""
    entries = json.loads(LIST_FILE.read_text(encoding="utf-8"))["folders"]
    loaded = {entry["name"]: entry.get("aliases", []) for entry in entries}
    for name in loaded:
        (LIBRARY / name).mkdir(exist_ok=True)
    folders.clear()
    folders.update(loaded)
    _index_aliases()
    _refresh_max_id()
    logger.info(f"表情包库：{len(folders)} 个文件夹，{len(aliases)} 个别名")


def _save() -> None:
    atomic_write_json(
        LIST_FILE,
        {"folders": [{"name": n, "aliases": a} for n, a in folders.items()]},
    )
    _index_aliases()


def _index_aliases() -> None:
    aliases.clear()
    aliases.update({a: name for name, names in folders.items() for a in names})


def resolve(name: str) -> str | None:
    """文件夹名或别名 → 文件夹名"""
    return name if name in folders else aliases.get(name)


def _order(path: Path) -> tuple:
    return (0, int(path.stem)) if path.stem.isdecimal() else (1, path.name)


def images(folder: str) -> list[Path]:
    """按编号排序，编号小的先入库"""
    files = [
        path
        for path in (LIBRARY / folder).iterdir()
        if path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(files, key=_order)


def all_images() -> list[Path]:
    return [path for folder in folders for path in images(folder)]


def find_by_id(sticker_id: int) -> Path | None:
    for folder in folders:
        for path in (LIBRARY / folder).glob(f"{sticker_id}.*"):
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                return path
    return None


def random_images(folder: str | None, count: int) -> list[Path]:
    """folder 为 None 时从全库抽，不重复"""
    pool = images(folder) if folder else all_images()
    return random.sample(pool, min(count, len(pool)))


def _refresh_max_id() -> None:
    global _max_id
    _max_id = max(
        (int(path.stem) for path in all_images() if path.stem.isdecimal()), default=0
    )


def add_image(folder: str, data: bytes, extension: str) -> Path:
    global _max_id
    _max_id += 1
    path = LIBRARY / folder / f"{_max_id}.{extension}"
    atomic_write_bytes(path, data)
    return path


def add_folder(name: str) -> None:
    (LIBRARY / name).mkdir(exist_ok=True)
    folders[name] = []
    _save()


def add_alias(alias: str, folder: str) -> None:
    folders[folder].append(alias)
    _save()


def remove_alias(alias: str) -> None:
    folders[aliases[alias]].remove(alias)
    _save()


def move_to_backup(paths: list[Path]) -> int:
    """移到 backup_duplicates/，文件名前面加上文件夹和时间，方便找回"""
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    moved = 0
    for path in paths:
        # 预览和确认之间可能已经被「删除」命令删掉了
        if path.exists():
            path.rename(BACKUP_DIR / f"{path.parent.name}_{stamp}_{path.name}")
            moved += 1
    return moved


def renumber() -> int:
    """按文件夹顺序把全库重新编成 1..N

    先全部改成临时名，避免和还没改的编号撞名；中途失败不回滚，已改的保留临时名。
    """
    staged = []
    for path in all_images():
        temp = path.with_name(f"tmp_{uuid.uuid4().hex}{path.suffix}")
        path.rename(temp)
        staged.append(temp)
    for number, temp in enumerate(staged, 1):
        temp.rename(temp.with_name(f"{number}{temp.suffix}"))
    _refresh_max_id()
    logger.info(f"表情包库重新编号完成：1 至 {len(staged)}")
    return len(staged)


load()
