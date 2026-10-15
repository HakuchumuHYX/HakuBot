import nonebot

# 导入各个模块
from plugins.add_friend import config
from plugins.add_friend import data_manager
from plugins.add_friend import request_handler
from plugins.add_friend import command_handler
from plugins.add_friend import utils

# 导出模块，便于其他插件引用
__all__ = ["config", "data_manager", "request_handler", "command_handler", "utils"]
