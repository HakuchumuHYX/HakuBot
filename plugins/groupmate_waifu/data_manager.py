"""
groupmate_waifu/data_manager.py
数据管理模块：统一管理数据文件的加载、保存和访问
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, Set, Any

from nonebot.log import logger

from .config import Config
import nonebot


# --- 配置加载 ---

_global_config = nonebot.get_driver().config
waifu_config = Config.parse_obj(_global_config.dict())

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


# --- 数据类型转换辅助函数 ---

def _convert_keys_to_int(data: Dict) -> Dict:
    """将字典的字符串键转换为整数键（递归）"""
    result = {}
    for key, value in data.items():
        try:
            int_key = int(key)
        except (ValueError, TypeError):
            int_key = key
        
        if isinstance(value, dict):
            result[int_key] = _convert_keys_to_int(value)
        elif isinstance(value, list):
            # 可能是 set 的 list 表示
            result[int_key] = value
        else:
            result[int_key] = value
    return result


def _convert_keys_to_str(data: Dict) -> Dict:
    """将字典的整数键转换为字符串键（递归），用于 JSON 保存"""
    result = {}
    for key, value in data.items():
        str_key = str(key)
        
        if isinstance(value, dict):
            result[str_key] = _convert_keys_to_str(value)
        elif isinstance(value, set):
            result[str_key] = list(value)
        else:
            result[str_key] = value
    return result


def _convert_list_values_to_set(data: Dict) -> Dict:
    """将字典中的 list 值转换为 set（用于 protect_list）"""
    result = {}
    for key, value in data.items():
        try:
            int_key = int(key)
        except (ValueError, TypeError):
            int_key = key
        
        if isinstance(value, list):
            result[int_key] = set(value)
        else:
            result[int_key] = value
    return result


# --- 数据加载 ---

def _load_json(file: Path, default: Any = None) -> Any:
    """从 JSON 文件加载数据"""
    if default is None:
        default = {}
    
    if not file.exists():
        logger.info(f"{file} 未找到，返回默认值。")
        return default
    
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                logger.info(f"{file} 为空，返回默认值。")
                return default
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"加载 {file} 失败 (JSON 解析错误): {e}。返回默认值。")
        return default
    except Exception as e:
        logger.error(f"加载 {file} 失败: {e}。返回默认值。")
        return default


def _load_legacy(file: Path) -> Any:
    """从旧格式文件加载数据（使用 eval，仅用于迁移）"""
    if not file.exists():
        return None
    
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return None
            return eval(content)
    except Exception as e:
        logger.error(f"加载旧格式文件 {file} 失败: {e}")
        return None


def _should_reset_file(file: Path) -> bool:
    """检查文件是否应该重置（基于修改时间）"""
    if not file.exists():
        return False
    
    try:
        file_mtime = os.path.getmtime(file)
        return file_mtime <= Zero_today
    except Exception:
        return False


def _load_record(json_file: Path, legacy_file: Path, apply_reset: bool) -> Dict:
    """
    加载记录数据，优先使用 JSON 文件，如果不存在则尝试迁移旧文件
    
    Args:
        json_file: JSON 文件路径
        legacy_file: 旧格式文件路径
        apply_reset: 是否应用重置逻辑
    
    Returns:
        记录数据字典
    """
    # 先尝试加载 JSON 文件
    if json_file.exists():
        if apply_reset and _should_reset_file(json_file):
            logger.info(f"{json_file} 是旧文件，已重置。")
            return {}
        
        data = _load_json(json_file)
        return _convert_keys_to_int(data)
    
    # 尝试从旧文件迁移
    if legacy_file.exists():
        if apply_reset and _should_reset_file(legacy_file):
            logger.info(f"{legacy_file} 是旧文件，已重置（迁移跳过）。")
            return {}
        
        data = _load_legacy(legacy_file)
        if data is not None:
            logger.info(f"从旧文件 {legacy_file} 迁移数据到 {json_file}")
            # 保存为新格式
            _save_json(json_file, data)
            # 可选：删除旧文件（这里选择保留）
            return data if isinstance(data, dict) else {}
    
    return {}


def _load_protect_list(json_file: Path, legacy_file: Path) -> Dict[int, Set[int]]:
    """加载保护名单（特殊处理：值为 set）"""
    # 先尝试加载 JSON 文件
    if json_file.exists():
        data = _load_json(json_file)
        return _convert_list_values_to_set(data)
    
    # 尝试从旧文件迁移
    if legacy_file.exists():
        data = _load_legacy(legacy_file)
        if data is not None:
            logger.info(f"从旧文件 {legacy_file} 迁移数据到 {json_file}")
            # 保存为新格式
            _save_json(json_file, _convert_keys_to_str(data))
            return data if isinstance(data, dict) else {}
    
    return {}


# --- 数据保存 ---

def _save_json(file: Path, data: Any):
    """保存数据到 JSON 文件"""
    try:
        # 转换为可 JSON 序列化的格式
        if isinstance(data, dict):
            serializable_data = _convert_keys_to_str(data)
        else:
            serializable_data = data
        
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(serializable_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 {file} 失败: {e}")


def save(file: Path, data: Any):
    """
    保存数据到文件
    如果 waifu_save 配置为 False，则不保存
    """
    if waifu_save:
        _save_json(file, data)


# --- 全局数据字典 ---

# CP 记录: {group_id: {user_id: waifu_id}}
record_CP: Dict[int, Dict[int, int]] = _load_record(
    RECORD_CP_FILE, _OLD_RECORD_CP_FILE, waifu_reset
)

# 被娶记录: {group_id: {waifu_id}}  -- 注意：实际存储时是 set，但加载后需要特殊处理
# 原代码中 record_waifu 是 {group_id: set()}，这里保持一致
_raw_record_waifu = _load_record(
    RECORD_WAIFU_FILE, _OLD_RECORD_WAIFU_FILE, waifu_reset
)
record_waifu: Dict[int, Set[int]] = {
    k: set(v) if isinstance(v, list) else v 
    for k, v in _raw_record_waifu.items()
}

# 锁定记录: {group_id: {user_id: waifu_id}}
record_lock: Dict[int, Dict[int, int]] = _load_record(
    RECORD_LOCK_FILE, _OLD_RECORD_LOCK_FILE, waifu_reset
)

# 透群友记录1: {user_id: count}
record_yinpa1: Dict[int, int] = _load_record(
    RECORD_YINPA1_FILE, _OLD_RECORD_YINPA1_FILE, waifu_reset
)

# 透群友记录2: {user_id: count}
record_yinpa2: Dict[int, int] = _load_record(
    RECORD_YINPA2_FILE, _OLD_RECORD_YINPA2_FILE, waifu_reset
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
