"""
Runtime state for groupmate_waifu.

This module parses plugin config, defines data file paths, initializes in-memory
records, and exposes save/reset entrypoints. JSON I/O and legacy migration live
in `storage.py`; business code should go through `service.py` instead of
accessing these records from handlers.
"""

import os
import time
from pathlib import Path
from typing import Dict, Set, Any

from nonebot.log import logger

from .config import Config
from .storage import (
    _convert_keys_to_int,
    _convert_keys_to_str,
    _convert_list_values_to_set,
    _load_json,
    _load_legacy,
    _load_protect_list,
    _load_record,
    _save_json,
)
import nonebot


# --- 配置加载 ---

_global_config = nonebot.get_driver().config
waifu_config = Config.model_validate(_global_config.dict())

# 导出配置项
waifu_save = waifu_config.waifu_save
waifu_reset = waifu_config.waifu_reset
last_sent_time_filter = waifu_config.waifu_last_sent_time_filter

# 概率阈值
HE = waifu_config.waifu_he
BE = HE + waifu_config.waifu_be  # BE 实际上是 HE + waifu_be
NTR = waifu_config.waifu_ntr

yinpa_HE = waifu_config.yinpa_he
yinpa_BE = yinpa_HE + waifu_config.yinpa_be
yinpa_CP = waifu_config.yinpa_cp
yinpa_CP = yinpa_HE if yinpa_CP == 0 else yinpa_CP


# --- 时间计算 ---

def _get_today_zero_timestamp() -> float:
    """获取今天零点的时间戳"""
    timestr = time.strftime('%Y-%m-%d', time.localtime(time.time()))
    timeArray = time.strptime(timestr, '%Y-%m-%d')
    return time.mktime(timeArray)


Zero_today = _get_today_zero_timestamp()


# --- 数据目录和文件路径 ---

WAIFU_DATA_DIR = Path() / "data" / "waifu"

if not WAIFU_DATA_DIR.exists():
    os.makedirs(WAIFU_DATA_DIR)

# 文件路径
RECORD_CP_FILE = WAIFU_DATA_DIR / "record_CP.json"
RECORD_WAIFU_FILE = WAIFU_DATA_DIR / "record_waifu.json"
RECORD_LOCK_FILE = WAIFU_DATA_DIR / "record_lock.json"
RECORD_YINPA1_FILE = WAIFU_DATA_DIR / "record_yinpa1.json"
RECORD_YINPA2_FILE = WAIFU_DATA_DIR / "record_yinpa2.json"
PROTECT_LIST_FILE = WAIFU_DATA_DIR / "list_protect.json"

# 旧文件路径（用于迁移）
_OLD_RECORD_CP_FILE = WAIFU_DATA_DIR / "record_CP"
_OLD_RECORD_WAIFU_FILE = WAIFU_DATA_DIR / "record_waifu"
_OLD_RECORD_LOCK_FILE = WAIFU_DATA_DIR / "record_lock"
_OLD_RECORD_YINPA1_FILE = WAIFU_DATA_DIR / "record_yinpa1"
_OLD_RECORD_YINPA2_FILE = WAIFU_DATA_DIR / "record_yinpa2"
_OLD_PROTECT_LIST_FILE = WAIFU_DATA_DIR / "list_protect"



# --- 数据保存 ---

def save(file: Path, data: Any):
    """
    保存数据到文件
    如果 waifu_save 配置为 False，则不保存
    """
    if waifu_save:
        _save_json(file, data)


# --- 全局数据字典 ---

# CP records are bidirectional for couples. `user_id -> user_id` means single today.
record_CP: Dict[int, Dict[int, int]] = _load_record(
    RECORD_CP_FILE, _OLD_RECORD_CP_FILE, waifu_reset, Zero_today
)

# Stores the "waifu side" of each couple so CP list rendering does not duplicate pairs.
_raw_record_waifu = _load_record(
    RECORD_WAIFU_FILE, _OLD_RECORD_WAIFU_FILE, waifu_reset, Zero_today
)
record_waifu: Dict[int, Set[int]] = {
    k: set(v) if isinstance(v, list) else v 
    for k, v in _raw_record_waifu.items()
}

# Locked couples are also stored bidirectionally, matching record_CP lookup style.
record_lock: Dict[int, Dict[int, int]] = _load_record(
    RECORD_LOCK_FILE, _OLD_RECORD_LOCK_FILE, waifu_reset, Zero_today
)

# 透群友记录1: {user_id: count}
record_yinpa1: Dict[int, int] = _load_record(
    RECORD_YINPA1_FILE, _OLD_RECORD_YINPA1_FILE, waifu_reset, Zero_today
)

# 透群友记录2: {user_id: count}
record_yinpa2: Dict[int, int] = _load_record(
    RECORD_YINPA2_FILE, _OLD_RECORD_YINPA2_FILE, waifu_reset, Zero_today
)

# 保护名单: {group_id: set(user_ids)}
protect_list: Dict[int, Set[int]] = _load_protect_list(
    PROTECT_LIST_FILE, _OLD_PROTECT_LIST_FILE
)


# --- 便捷保存函数 ---

def save_record_CP():
    """保存 CP 记录"""
    save(RECORD_CP_FILE, record_CP)


def save_record_waifu():
    """保存被娶记录"""
    save(RECORD_WAIFU_FILE, record_waifu)


def save_record_lock():
    """保存锁定记录"""
    save(RECORD_LOCK_FILE, record_lock)


def save_record_yinpa1():
    """保存透群友记录1"""
    save(RECORD_YINPA1_FILE, record_yinpa1)


def save_record_yinpa2():
    """保存透群友记录2"""
    save(RECORD_YINPA2_FILE, record_yinpa2)


def save_protect_list():
    """保存保护名单"""
    save(PROTECT_LIST_FILE, protect_list)


# --- 重置函数 ---

def reset_all_records():
    """
    重置所有记录（由定时任务调用）
    根据 waifu_reset 配置决定重置范围
    """
    global record_CP, record_waifu, record_lock, record_yinpa1, record_yinpa2
    
    if waifu_reset:
        # 完全重置
        record_CP.clear()
        record_waifu.clear()
        record_lock.clear()
        record_yinpa1.clear()
        record_yinpa2.clear()
        
        save_record_CP()
        save_record_waifu()
        save_record_lock()
        save_record_yinpa1()
        save_record_yinpa2()
        
        logger.info("娶群友记录已重置 (waifu_reset=True)")
    else:
        # 只重置单身记录和涩涩记录
        users_to_remove = []
        for group_id, cp_data in list(record_CP.items()):
            for user_id, waifu_id in list(cp_data.items()):
                if user_id == waifu_id:
                    users_to_remove.append((group_id, user_id))
        
        for group_id, user_id in users_to_remove:
            if group_id in record_CP and user_id in record_CP[group_id]:
                del record_CP[group_id][user_id]
        
        record_yinpa1.clear()
        record_yinpa2.clear()
        
        save_record_CP()
        save_record_yinpa1()
        save_record_yinpa2()
        
        logger.info("娶群友记录已重置 (waifu_reset=False)")
