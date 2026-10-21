"""应用装配：由 bot.py 显式调用，导入模块本身不注册任何生命周期回调"""

import asyncio
from nonebot import logger
from utils.paths import project_root
from core import access, cooldown
from core.lifecycle import runtime


def configure(driver):
    from utils.paths import PluginPaths

    for directory in (project_root() / "plugins").iterdir():
        if (directory / "__init__.py").is_file() and directory.name.isidentifier():
            PluginPaths(directory.name).data.mkdir(parents=True, exist_ok=True)
    access.load()
    cooldown.load()
    from utils.network.http import close_sessions
    from utils.rendering.browser import browser_pool
    from utils.files import close_temporary_files

    runtime.closers.extend((close_sessions, browser_pool.close, close_temporary_files))

    # NoneBot 自己的 "Bot N connected" 是 INFO，被 bot.py 的 stdout 过滤掉了，这里补一行
    @driver.on_bot_connect
    async def log_bot_connect(bot):
        logger.info(f"Bot {bot.self_id} 已连接")

    @driver.on_startup
    async def start_shared_resources():
        from utils.onebot.media import cleanup_outbound_media

        async def cleanup():
            while True:
                await asyncio.to_thread(cleanup_outbound_media)
                await asyncio.sleep(3600)

        runtime.spawn(cleanup(), name="shared-cache-cleanup")

    @driver.on_shutdown
    async def stop_shared_resources():
        await runtime.close()


def finalize(driver):
    """NoneBot 按注册的逆序执行关闭钩子，这里最后注册，所以最先停掉产生任务的一方"""

    @driver.on_shutdown
    async def cancel_background_work():
        from nonebot import get_plugin

        if get_plugin("nonebot_plugin_apscheduler") is not None:
            from nonebot_plugin_apscheduler import scheduler

            if scheduler.running:
                scheduler.pause()
        await runtime.cancel_tasks()
