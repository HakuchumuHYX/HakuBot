"""
groupmate_waifu/__init__.py
娶群友插件主入口

功能：
- 娶群友：随机或指定娶一个群友
- 透群友：随机或指定透一个群友
- 保护名单：保护指定成员不被娶/透
- 离婚：与 CP 分手
- 列表查询：查看卡池、CP 列表、涩涩记录
"""

from nonebot import require
from nonebot.plugin import PluginMetadata
from nonebot.plugin.on import on_command
from nonebot.permission import SUPERUSER

from .config import Config
from .constants import PLUGIN_NAME


# --- 插件元数据 ---

__plugin_meta__ = PluginMetadata(
    name="娶群友",
    description="娶群友、透群友等互动功能",
    usage="""命令列表：
- 娶群友 [@某人]：随机或指定娶一个群友
- 透群友 [@某人]：随机或指定透一个群友
- 离婚/分手：与 CP 分手
- 娶群友保护 [@某人]：保护自己或他人
- 解除娶群友保护 [@某人]：解除保护
- 查看保护名单：查看当前群的保护名单
- 查看群友卡池/群友卡池：查看可娶群友
- 本群CP/本群cp：查看本群 CP 列表
- 涩涩记录/色色记录：查看透群友记录
- 重置记录（管理员）：手动重置记录
""",
    config=Config,
    extra={
        "author": "HakuBot",
        "version": "2.0.0",
    }
)


# --- 定时任务：每日重置记录 ---

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .data_manager import reset_all_records, waifu_reset

# 注册定时任务
scheduler.add_job(reset_all_records, "cron", hour=0, misfire_grace_time=120)

# 注册手动重置命令
reset_command = on_command("重置记录", permission=SUPERUSER, priority=10, block=True)
reset_command.append_handler(reset_all_records)


# --- 导入子模块，触发 Matcher 注册 ---

from . import marry
from . import yinpa
