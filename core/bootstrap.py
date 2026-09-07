"""Application assembly; module imports do not register lifecycle callbacks."""

import asyncio
from utils.paths import project_root
from core import access_state
from core.lifecycle import runtime

_installed = False


def configure(driver):
    global _installed
    if _installed:
        return
    _installed = True
    from utils.paths import PluginPaths

    for directory in (project_root() / "plugins").iterdir():
        if (directory / "__init__.py").is_file() and directory.name.isidentifier():
            PluginPaths(directory.name).data.mkdir(parents=True, exist_ok=True)
    access_state.initialize()
    from utils.network.http import close_sessions
    from utils.rendering.browser import browser_pool
    from utils.concurrency import close_pool
    from utils.files import close_temporary_files

    runtime.closers.extend(
        (close_pool, close_sessions, browser_pool.close, close_temporary_files)
    )

    @driver.on_startup
    async def start_shared_resources():
        from utils.rendering.cache import cleanup_render_cache
        from utils.onebot.media import cleanup_outbound_media

        async def cleanup():
            while True:
                await asyncio.to_thread(cleanup_render_cache)
                await asyncio.to_thread(cleanup_outbound_media)
                await asyncio.sleep(3600)

        runtime.spawn(cleanup(), name="shared-cache-cleanup")

    @driver.on_shutdown
    async def stop_shared_resources():
        await runtime.close()


def finalize(driver):
    """NoneBot shuts down in reverse registration order: cancel producers first."""
    @driver.on_shutdown
    async def cancel_background_work():
        from nonebot import get_plugin

        if get_plugin("nonebot_plugin_apscheduler") is not None:
            from nonebot_plugin_apscheduler import scheduler

            if scheduler.running:
                scheduler.pause()
        await runtime.cancel_tasks()
