import json
from pathlib import Path
from nonebot.log import logger
from typing import Set

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"


class Config:
    """插件配置类"""

    def __init__(self):
        self.plus_one_priority = 1
        self.plus_one_black_list = []
        self.blocked_words = set()
        self.load_config()

    def load_config(self):
        """从 JSON 文件加载配置"""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)

                self.plus_one_priority = config_data.get("plus_one_priority", 1)
                self.plus_one_black_list = config_data.get("plus_one_black_list", [])

                # 将列表转换为集合
                blocked_words_list = config_data.get("blocked_words", [])
                self.blocked_words = set(blocked_words_list)

                logger.info("成功加载复读姬插件配置")
            else:
                # 如果配置文件不存在，创建默认配置
                self.create_default_config()
                logger.warning("配置文件不存在，已创建默认配置文件")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}，使用默认配置")
            self.set_default_values()

    def create_default_config(self):
        """创建默认配置文件"""
        default_config = {
            "plus_one_priority": 1,
            "plus_one_black_list": [],
            "blocked_words": [
            ]
        }

        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)

            # 设置默认值
            self.plus_one_priority = default_config["plus_one_priority"]
            self.plus_one_black_list = default_config["plus_one_black_list"]
            self.blocked_words = set(default_config["blocked_words"])

        except Exception as e:
            logger.error(f"创建默认配置文件失败: {e}")
            self.set_default_values()

    def set_default_values(self):
        """设置默认值"""
        self.plus_one_priority = 1
        self.plus_one_black_list = []
        self.blocked_words = {
        }

    def save_config(self):
        """保存配置到 JSON 文件"""
        config_data = {
            "plus_one_priority": self.plus_one_priority,
            "plus_one_black_list": self.plus_one_black_list,
            "blocked_words": list(self.blocked_words)  # 将集合转换为列表
        }

        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            logger.info("成功保存复读姬插件配置")
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False


# 创建全局配置实例
config = Config()