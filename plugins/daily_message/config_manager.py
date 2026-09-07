from utils.json_io import atomic_write_json
from utils.paths import PluginPaths
import json
from pathlib import Path
from nonebot.log import logger

# 配置文件路径
CONFIG_FILE = PluginPaths("daily_message").config / "config.json"


def load_config():
    """加载配置文件"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            logger.error("配置文件不存在")
            return {"schedules": []}
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")
        return {"schedules": []}


def save_config(config_data):
    """保存配置文件"""
    try:
        atomic_write_json(CONFIG_FILE, config_data, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"保存配置文件失败: {e}")
        return False
