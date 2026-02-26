"""
deer_pipe - 🦌管签到插件

一个基于 NoneBot2 的趣味签到插件，支持每日签到、补签和日历查看功能。
"""

from nonebot import logger
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

# 加载依赖插件
from . import requirements as requirements

# 加载命令处理器
from . import matchers as matchers

# 导出配置供外部使用
from .config import config as plugin_config
from .constants import PLUGIN_ID, PLUGIN_VERSION

__all__ = ["plugin_config", "PLUGIN_ID", "PLUGIN_VERSION"]

# 插件元数据
__plugin_meta__ = PluginMetadata(
    name="🦌管签到",
    description="一个🦌管签到插件，支持每日签到、补签和日历查看",
    usage=(
        '发送"🦌帮助"以查看插件命令\n'
        "主要命令：\n"
        "  🦌 - 签到\n"
        "  🦌 @xxx - 帮他人签到\n"
        "  补🦌 x - 补签本月x日\n"
        "  🦌历 - 查看签到日历\n"
        "  🦌帮助 - 查看帮助"
    ),
    type="application",
    homepage="https://github.com/SamuNatsu/nonebot-plugin-deer-pipe",
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna",
        "nonebot_plugin_apscheduler",
        "nonebot_plugin_localstore",
        "nonebot_plugin_userinfo",
    ),
    extra={
        "version": PLUGIN_VERSION,
        "author": "SamuNatsu",
    },
)

logger.info(f"deer_pipe 插件 v{PLUGIN_VERSION} 加载完成")
