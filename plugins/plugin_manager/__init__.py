# plugin_manager/__init__.py
import json
import os
from pathlib import Path
from typing import Dict, Any

# --- 路径定义 ---
DATA_DIR = Path("data/plugin_manager")
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATUS_DATA_FILE = DATA_DIR / "plugin_status.json"
README_FILE = Path(__file__).parent / "readme.md"
CD_CONFIG_FILE = DATA_DIR / "cd_config.json"
CD_RUNTIME_FILE = DATA_DIR / "cd_runtime.json"


# --- Readme 解析 ---
def load_readme_plugins() -> Dict[str, str]:
    """从 readme.md 加载插件列表"""
    plugins = {}
    if not README_FILE.exists():
        return plugins
    try:
        with open(README_FILE, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.split('\n')
        for line in lines:
            if '：' in line or ':' in line:
                separator = '：' if '：' in line else ':'
                if separator in line:
                    parts = line.split(separator, 1)
                    if len(parts) == 2:
                        plugin_name = parts[0].strip()
                        plugin_id = parts[1].strip()
                        plugins[plugin_id] = plugin_name
    except Exception as e:
        print(f"读取 readme.md 失败: {e}")
    return plugins


# --- 插件开关 I/O ---
def load_plugin_status() -> Dict[str, Dict[str, bool]]:
    """加载插件状态数据"""
    if STATUS_DATA_FILE.exists():
        try:
            with open(STATUS_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"插件状态文件 {STATUS_DATA_FILE} 损坏，将创建新文件。")
            return {}
    return {}


def save_plugin_status(data: Dict[str, Dict[str, bool]]):
    """保存插件状态数据"""
    with open(STATUS_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- CD 管理 I/O ---
def load_cd_config() -> Dict[str, Dict[str, int]]:
    """加载插件CD时长配置 (已更新为分群结构)"""
    if CD_CONFIG_FILE.exists():
        try:
            with open(CD_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"CD配置文件 {CD_CONFIG_FILE} 损坏，将创建新文件。")
            return {}
    return {}


def save_cd_config(data: Dict[str, Dict[str, int]]):
    """保存插件CD时长配置 (已更新为分群结构)"""
    with open(CD_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cd_runtime() -> Dict[str, Dict[str, Dict[str, float]]]:
    """加载用户CD运行时数据"""
    if CD_RUNTIME_FILE.exists():
        try:
            with open(CD_RUNTIME_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"CD运行时文件 {CD_RUNTIME_FILE} 损坏，将创建新文件。")
            return {}
    return {}


def save_cd_runtime(data: Dict[str, Dict[str, Dict[str, float]]]):
    """保存用户CD运行时数据"""
    with open(CD_RUNTIME_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- 全局数据缓存 ---
plugin_status = load_plugin_status()
cd_config = load_cd_config()
cd_runtime = load_cd_runtime()
