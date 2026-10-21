"""
deer_pipe - 🦌管签到插件

一个基于 NoneBot2 的趣味签到插件，支持每日签到、补签和日历查看功能。
"""

# 加载依赖插件
from plugins.deer_pipe import requirements as requirements

# 加载命令处理器
from plugins.deer_pipe import matchers as matchers

# 导出配置供外部使用
from plugins.deer_pipe.config import config as plugin_config
from plugins.deer_pipe.constants import PLUGIN_ID

__all__ = ["plugin_config", "PLUGIN_ID"]
