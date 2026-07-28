from __future__ import annotations

from nonebot import get_driver
from nonebot.plugin import PluginMetadata
from loguru import logger

from .config import BridgeConfig
from .capture import register_capture_hooks
from .capture import capture_manager
from .diagnostics import DiagnosticCapture
from .persistence import PersistenceWriter
from .status import (
    BridgeRuntime,
    bot_status_manager,
    register_bot_status_hooks,
)

__plugin_meta__ = PluginMetadata(
    name="WebConsole Bridge",
    description="异步采集 HakuBot 真实事件响应、诊断日志和当前在线状态",
    usage="无用户命令",
    type="application",
    homepage="",
    supported_adapters={"~onebot.v11"},
)

try:
    bridge_config = BridgeConfig.from_file()
except ValueError as exc:
    logger.bind(webconsole_bridge_internal=True).warning(
        "WebConsole bridge configuration is invalid; event collection is "
        f"disabled: {exc}"
    )
    bridge_config = BridgeConfig.disabled()
bridge_runtime = BridgeRuntime(bridge_config)
persistence_writer = PersistenceWriter(bridge_config)
diagnostic_capture = DiagnosticCapture(bridge_runtime, persistence_writer)
bridge_runtime.set_availability_callback(
    lambda available: (
        persistence_writer.start() if available else persistence_writer.stop()
    )
)
capture_manager.set_finalize_callback(persistence_writer.persist_capture)
bot_status_manager.set_write_callback(persistence_writer.persist_status)
register_capture_hooks(bridge_runtime)

try:
    driver = get_driver()
except ValueError:
    driver = None

if driver is not None:
    register_bot_status_hooks(driver, bridge_runtime)

    @driver.on_startup
    async def _start_webconsole_bridge() -> None:
        await bridge_runtime.start()
        diagnostic_capture.start()
        await bot_status_manager.start()

    @driver.on_shutdown
    async def _stop_webconsole_bridge() -> None:
        await bot_status_manager.stop()
        diagnostic_capture.stop()
        await bridge_runtime.stop()
