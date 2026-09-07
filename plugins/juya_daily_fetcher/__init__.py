from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Event, PrivateMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from utils.logging import get_exc_desc, get_logger
from plugins.juya_daily_fetcher.config import Config, plugin_config
from plugins.juya_daily_fetcher import scheduler as _scheduler
from plugins.juya_daily_fetcher.scheduler import run_debug

logger = get_logger("juya_daily_fetcher")

__plugin_meta__ = PluginMetadata(
    name="橘鸦 AI 早报",
    description="订阅橘鸦 AI 早报 RSS，渲染原文长图并合并转发到指定群",
    usage="超管私聊命令：早报测试（只发到配置的 debug 私聊，不改生产 seen）",
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

debug_cmd = on_command("早报测试", permission=SUPERUSER, priority=5, block=True)


@debug_cmd.handle()
async def handle_debug(bot: Bot, event: Event):
    if not isinstance(event, PrivateMessageEvent):
        await debug_cmd.finish("早报测试只能在私聊使用")
    if not plugin_config.debug_user_id:
        await debug_cmd.finish("未配置 debug_user_id，无法发送早报测试")
    if str(event.user_id) != plugin_config.debug_user_id:
        await debug_cmd.finish("早报测试只能发到指定 debug 私聊")
    await debug_cmd.send("正在拉取并渲染最新一期早报，请稍候...")
    try:
        result = await run_debug(bot)
    except Exception as exc:
        logger.exception(f"早报测试失败: {get_exc_desc(exc)}")
        await debug_cmd.finish(f"早报测试失败：{get_exc_desc(exc)}")
    await debug_cmd.finish(result)
