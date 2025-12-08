import json
from pathlib import Path
from nonebot.log import logger

# 获取当前文件所在目录，确保能找到同级目录下的 config.json
PLUGIN_DIR = Path(__file__).parent
CONFIG_FILE = PLUGIN_DIR / "config.json"


def load_config() -> dict:
    """读取配置，如果不存在则写入默认配置"""
    default_config = {
        "url": "http://example.com/",
        "token": "your_token_here",
        "watermark": "Created by NoneBot"  # 水印文字
    }

    if not CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=4, ensure_ascii=False)
            logger.warning(f"配置文件 {CONFIG_FILE} 不存在，已创建默认配置，请修改后重启或重试。")
            return default_config
        except Exception as e:
            logger.error(f"创建默认配置文件失败: {e}")
            return default_config

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        return default_config
    