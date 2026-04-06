"""角色昵称映射模块"""
import json
from pathlib import Path
from typing import Optional, Dict, List
from nonebot.log import logger

from .config import data_dir

NICKNAMES_FILE = data_dir / "nicknames.json"

# 昵称 -> 角色ID 映射
_nickname_to_cid: Dict[str, int] = {}
# 角色ID -> 第一个昵称
_cid_to_name: Dict[int, str] = {}


def load_nicknames():
    """加载角色昵称数据"""
    global _nickname_to_cid, _cid_to_name
    _nickname_to_cid.clear()
    _cid_to_name.clear()

    try:
        with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            cid = item["id"]
            nicknames = item["nicknames"]
            if nicknames:
                _cid_to_name[cid] = nicknames[0]
            for nick in nicknames:
                _nickname_to_cid[nick.lower()] = cid
        logger.info(f"已加载 {len(_cid_to_name)} 个角色的昵称数据")
    except Exception as e:
        logger.error(f"加载角色昵称数据失败: {e}")


def get_cid_by_nickname(text: str) -> Optional[int]:
    """根据昵称获取角色ID"""
    text = text.strip().lower()
    return _nickname_to_cid.get(text)


def get_character_name_by_id(cid: int) -> str:
    """根据角色ID获取显示名称"""
    return _cid_to_name.get(cid, f"角色{cid}")


# 模块加载时自动加载昵称数据
load_nicknames()
