"""Group access state, initialized explicitly by the application."""

from typing import Dict

from utils.json_io import atomic_write_json, load_json
from utils.paths import PluginPaths

# --- 路径定义 ---
DATA_DIR = PluginPaths("plugin_manager").data


STATUS_DATA_FILE = DATA_DIR / "plugin_status.json"

CD_CONFIG_FILE = DATA_DIR / "cd_config.json"


# --- 插件开关 I/O ---
def load_plugin_status() -> Dict[str, Dict[str, bool]]:
    return _load_state_file(STATUS_DATA_FILE)


def save_plugin_status(data: Dict[str, Dict[str, bool]]):
    """保存插件状态数据"""
    atomic_write_json(STATUS_DATA_FILE, data, indent=2, ensure_ascii=False)


# --- CD 管理 I/O ---
def load_cd_config() -> Dict[str, Dict[str, int]]:
    return _load_state_file(CD_CONFIG_FILE)


def save_cd_config(data: Dict[str, Dict[str, int]]):
    """保存 CD 时长：{群号: {功能 ID: 秒}}"""
    atomic_write_json(CD_CONFIG_FILE, data, indent=2, ensure_ascii=False)


plugin_status = {}
cd_config = {}


def initialize():
    plugin_status.clear()
    plugin_status.update(load_plugin_status())
    cd_config.clear()
    cd_config.update(load_cd_config())


def _load_state_file(path):
    result = load_json(
        path, expected_type=dict, default={}, missing_ok=True, backup_on_error=True
    )
    if not result.success:
        raise ValueError(f"Core state is invalid: {path.name}") from result.error
    return result.data
