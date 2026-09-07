"""Storage helpers for groupmate_waifu runtime JSON files."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Set

from nonebot.log import logger

from utils.json_io import atomic_write_json


def _convert_keys_to_int(data: Dict) -> Dict:
    result = {}
    for key, value in data.items():
        try:
            int_key = int(key)
        except (ValueError, TypeError):
            int_key = key

        if isinstance(value, dict):
            result[int_key] = _convert_keys_to_int(value)
        elif isinstance(value, list):
            result[int_key] = value
        else:
            result[int_key] = value
    return result


def _convert_keys_to_str(data: Dict) -> Dict:
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


def _load_json(file: Path, default: Any = None) -> Any:
    if default is None:
        default = {}

    if not file.exists():
        logger.info(f"{file} 未找到，返回默认值。")
        return default

    try:
        with open(file, "r", encoding="utf-8") as f:
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


def _should_reset_file(file: Path, zero_timestamp: float) -> bool:
    if not file.exists():
        return False

    try:
        file_mtime = os.path.getmtime(file)
        return file_mtime <= zero_timestamp
    except Exception:
        return False


def _load_record(
    json_file: Path,
    apply_reset: bool,
    zero_timestamp: float,
) -> Dict:
    if apply_reset and _should_reset_file(json_file, zero_timestamp):
        logger.info(f"{json_file} 是旧文件，已重置。")
        return {}
    return _convert_keys_to_int(_load_json(json_file))


def _load_protect_list(json_file: Path) -> Dict[int, Set[int]]:
    return _convert_list_values_to_set(_load_json(json_file))


def _save_json(file: Path, data: Any):
    try:
        if isinstance(data, dict):
            serializable_data = _convert_keys_to_str(data)
        else:
            serializable_data = data

        atomic_write_json(file, serializable_data)
    except Exception as e:
        logger.error(f"保存 {file} 失败: {e}")
