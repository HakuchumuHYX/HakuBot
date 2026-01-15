from pydantic import BaseModel, Extra
from pathlib import Path
import json
from typing import List  # 引入 List

PLUGIN_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = PLUGIN_DIR / "config.json"


class HelpConfig(BaseModel, extra=Extra.ignore):
    help_text: List[str] = [
        "欢迎使用群高性能萝卜子ATRI！",
        "项目地址：https://github.com/HakuchumuHYX/HakuBot"
    ]
    # 如果 config.json 里还有 keywords 和 bot_name，也可以加上定义
    keywords: List[str] = ["help", "帮助"]
    bot_name: str = "ATRI帮助文档"


def load_config() -> HelpConfig:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return HelpConfig.parse_obj(json.load(f))
        except Exception as e:
            print(f"ERROR: 读取 config.json 失败: {e}")
            return HelpConfig()
    return HelpConfig()