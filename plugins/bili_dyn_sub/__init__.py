"""NoneBot 插件入口；命令在 handlers，定时任务在 scheduler（由 handlers 导入时注册）"""

from . import handlers  # noqa: F401
