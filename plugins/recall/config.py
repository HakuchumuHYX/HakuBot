import json
from pathlib import Path
from nonebot.log import logger

CONFIG_FILE = Path(__file__).parent / "config.json"

class Config:
    def __init__(self):
        self.target_group: str = ""
        self.load_config()

    def load_config(self):
        if not CONFIG_FILE.exists():
            self.save_config()
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.target_group = data.get("target_group", "")
        except Exception as e:
            logger.error(f"加载撤回插件配置失败: {e}")

    def save_config(self):
        try:
            data = {"target_group": self.target_group}
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存撤回插件配置失败: {e}")

config = Config()
