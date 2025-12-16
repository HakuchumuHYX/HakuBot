import logging
import asyncio
import sys
import os
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from multiprocessing import freeze_support  # 1. 导入 freeze_support

logging.getLogger('asyncio').setLevel(logging.ERROR)


class AsyncioErrorFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if "OSError: [WinError 10038]" in msg:
            return False
        if "ProactorBasePipeTransport" in msg:
            return False
        if "10038" in msg:
            return False
        return True


asyncio_logger = logging.getLogger('asyncio')
asyncio_logger.addFilter(AsyncioErrorFilter())
root_logger = logging.getLogger()
root_logger.addFilter(AsyncioErrorFilter())
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
os.environ['PYTHONASYNCIODEBUG'] = '0'


if __name__ == "__main__":
    freeze_support()
    nonebot.init()

    driver = nonebot.get_driver()
    driver.register_adapter(ONEBOT_V11Adapter)

    nonebot.load_builtin_plugins("echo")
    nonebot.load_from_toml("pyproject.toml")

    nonebot.run()
