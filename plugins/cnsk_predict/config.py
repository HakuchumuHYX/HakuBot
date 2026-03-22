import json
from pathlib import Path
from nonebot.log import logger

# === 路径与常量定义 ===
BASE_DIR = Path(__file__).parent
DATA_DIR = Path() / "data" / "sekai_cache"
CONFIG_FILE = BASE_DIR / "config.json"

# 确保缓存目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 常量
FILE_CLEAN_SECONDS = 24 * 60 * 60
AUTO_REFRESH_INTERVAL = 5  # 单位：分钟


def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_FILE.exists():
        logger.warning("config.json 不存在，使用默认配置")
        return {
            "url_home": "https://snowyviewer.exmeaning.com/prediction/"
        }
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"配置文件读取错误: {e}")
        return {}
