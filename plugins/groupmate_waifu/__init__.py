"""
娶群友插件
娶群友（随机或指定，可 NTR、锁 CP）、透群友、离婚、保护名单，以及卡池、CP、涩涩记录查询。
记录按天清空，保护名单长期保存。
"""

from nonebot import get_driver
from nonebot.permission import SUPERUSER
from nonebot.plugin.on import on_command

from core.lifecycle import on_plugin_startup
from plugins.groupmate_waifu import store


@on_plugin_startup(get_driver(), "groupmate_waifu")
def _on_startup() -> None:
    store.load()


reset_command = on_command("重置记录", permission=SUPERUSER, priority=5, block=True)


@reset_command.handle()
async def handle_reset_command():
    store.reset()
    await reset_command.finish("娶群友记录已重置。")


from plugins.groupmate_waifu import marry, protect, yinpa  # noqa: E402, F401
