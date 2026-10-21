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
from nonebot.plugin.on import on_command
from nonebot.permission import SUPERUSER


# --- 定时任务：每日重置记录 ---

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from plugins.groupmate_waifu.service import reset_records

# 注册定时任务
scheduler.add_job(reset_records, "cron", hour=0, misfire_grace_time=120)

# 注册手动重置命令
reset_command = on_command("重置记录", permission=SUPERUSER, priority=5, block=True)


@reset_command.handle()
async def handle_reset_command():
    reset_records()
    await reset_command.finish("娶群友记录已重置。")


# Importing handlers registers all NoneBot matchers via module side effects.
from plugins.groupmate_waifu import handlers  # noqa: F401
