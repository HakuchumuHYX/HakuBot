import logging
import asyncio
import sys

# 方法1：直接设置 asyncio 日志级别
logging.getLogger('asyncio').setLevel(logging.ERROR)

# 方法2：创建并应用过滤器
class AsyncioErrorFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        # 过滤包含特定错误信息的日志
        if "OSError: [WinError 10038]" in msg:
            return False
        if "ProactorBasePipeTransport" in msg:
            return False
        if "10038" in msg:
            return False
        return True

# 将过滤器应用到 asyncio 日志记录器
asyncio_logger = logging.getLogger('asyncio')
asyncio_logger.addFilter(AsyncioErrorFilter())

# 方法3：同时应用到根日志记录器（确保捕获所有相关日志）
root_logger = logging.getLogger()
root_logger.addFilter(AsyncioErrorFilter())

# 方法4：设置更严格的异常处理
original_excepthook = sys.excepthook

def handle_exception(exc_type, exc_value, exc_traceback):
    if (issubclass(exc_type, OSError) and
        hasattr(exc_value, 'winerror') and
        exc_value.winerror == 10038):
        return
    if (isinstance(exc_value, OSError) and
        "10038" in str(exc_value)):
        return
    original_excepthook(exc_type, exc_value, exc_traceback)

sys.excepthook = handle_exception

# 设置环境变量（在导入其他模块之前）
import os
os.environ['PYTHONASYNCIODEBUG'] = '0'

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# 加载内置 echo 插件与 pyproject.toml 中的插件
nonebot.load_builtin_plugins("echo")
nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.logger.info("🤖 启动 NoneBot 中...")
    nonebot.run()