import nonebot
from nonebot import get_driver
from nonebot.log import logger

# 导入各个模块
from . import config
from . import data_manager
from . import request_handler
from . import command_handler
from . import utils

# 插件信息
__plugin_name__ = "好友请求处理"
__plugin_usage__ = """
自动处理好友请求：
- 验证信息中包含白名单群号的用户会自动通过
- 其他用户会被拒绝并收到提示消息
""".strip()

# 插件加载成功提示
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    logger.success("好友请求处理插件加载成功")
    logger.info(f"配置了 {len(config.AUTO_APPROVE_GROUPS)} 个自动同意群组")
    logger.info(f"当前有 {len(data_manager.request_manager.get_all_pending_requests())} 个待处理请求")

# 导出模块，便于其他插件引用
__all__ = [
    "config",
    "data_manager",
    "request_handler",
    "command_handler",
    "utils"
]