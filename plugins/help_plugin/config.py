from utils.paths import PluginPaths
from pydantic import BaseModel, Extra
from pathlib import Path
import json
from typing import List

from nonebot import logger

PLUGIN_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = PluginPaths("help_plugin").config / "config.json"


class HelpItem(BaseModel):
    name: str
    desc: str
    # 用户要发送的指令；为空表示自动触发
    triggers: List[str] = []
    # 致谢、注意事项等补充说明
    note: str = ""
    # 不画进图里，统一用合并转发发出，方便复制
    links: List[str] = []


class HelpSection(BaseModel):
    title: str
    items: List[HelpItem]


class HelpConfig(BaseModel, extra=Extra.ignore):
    bot_name: str = "ATRI"
    intro: str = "欢迎使用群高性能萝卜子 ATRI！"
    links: List[str] = ["https://github.com/HakuchumuHYX/HakuBot"]
    sections: List[HelpSection] = []


def load_config() -> HelpConfig:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return HelpConfig.parse_obj(json.load(f))
        except Exception as e:
            logger.exception(f"读取 config.json 失败: {e}")
            return HelpConfig()
    return HelpConfig()
