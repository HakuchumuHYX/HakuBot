"""绑定表和锐评表。

bindings.json：{QQ: {"pw": [账号...], "5e": [账号...]}}，按 QQ 全局生效，列表第一个是主号。
账号的 id 在完美是 SteamID64，在 5E 是 domain；5E 另存 uuid 省一次转换。
一个游戏账号可以被多个 QQ 绑定，否则先绑的人能占住别人的号。

roasts.json：{"career": {key: 锐评}, "match": {key: 锐评}}。
生涯锐评每个 key 只留最新一份，水位变了才重新生成；单局锐评按局存，对局不会变。
"""

from datetime import datetime

from nonebot import logger

from plugins.cs_stats.config import DATA_DIR
from utils.json_io import atomic_write_json, load_json

BINDINGS_FILE = DATA_DIR / "bindings.json"
ROASTS_FILE = DATA_DIR / "roasts.json"


def _load(path, default):
    result = load_json(path, default=default, missing_ok=True, backup_on_error=True)
    if not result.success:
        logger.error(f"[cs_stats] {path.name} 读取失败，已备份为 {result.backup_path}: {result.error}")
    return result.data


bindings: dict[str, dict[str, list[dict]]] = _load(BINDINGS_FILE, {})
roasts: dict[str, dict[str, dict]] = _load(ROASTS_FILE, {"career": {}, "match": {}})


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def accounts(qq: str, platform: str) -> list[dict]:
    return bindings.get(qq, {}).get(platform, [])


def add_account(qq: str, platform: str, account: dict) -> None:
    bindings.setdefault(qq, {}).setdefault(platform, []).append({**account, "bound_at": _now()})
    atomic_write_json(BINDINGS_FILE, bindings)


def remove_account(qq: str, platform: str, index: int) -> dict:
    removed = bindings[qq][platform].pop(index)
    atomic_write_json(BINDINGS_FILE, bindings)
    return removed


def set_main(qq: str, platform: str, index: int) -> None:
    items = bindings[qq][platform]
    items.insert(0, items.pop(index))
    atomic_write_json(BINDINGS_FILE, bindings)


def get_roast(kind: str, key: str) -> dict | None:
    return roasts.setdefault(kind, {}).get(key)


def save_roast(kind: str, key: str, roast: dict) -> None:
    roasts.setdefault(kind, {})[key] = {**roast, "created_at": _now()}
    atomic_write_json(ROASTS_FILE, roasts)
