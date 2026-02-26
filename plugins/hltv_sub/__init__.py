"""
HLTV 订阅插件入口
"""

from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_localstore")

from .config import Config
from .data_source import hltv_data
from .scheduler import setup_scheduler

# 显式初始化定时任务（替代 import side-effect）
setup_scheduler()

# 导入 handlers 以注册命令（import 即注册）
from . import handlers as _handlers  # noqa: F401,E402


__plugin_meta__ = PluginMetadata(
    name="HLTV订阅",
    description="HLTV CS2 赛事订阅和比赛信息查询",
    usage="""命令列表：
- event列表：查看近期大型赛事
- event订阅 [ID]：订阅赛事
- event取消订阅 [ID]：取消订阅
- 我的订阅：查看已订阅赛事

- matches列表：查看已订阅赛事的比赛
- results列表：查看已订阅赛事的结果
- stats：查看最新比赛数据
- stats [ID]：查看指定比赛数据

- hltv开启：开启本群功能
- hltv关闭：关闭本群功能

- hltv帮助：查看帮助
""",
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

driver = get_driver()


@driver.on_shutdown
async def cleanup():
    """清理资源"""
    await hltv_data.close()
