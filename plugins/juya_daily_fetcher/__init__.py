from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, Event, PrivateMessageEvent
from nonebot.permission import SUPERUSER

from plugins.juya_daily_fetcher.config import plugin_config
from plugins.juya_daily_fetcher.scheduler import send_debug
from utils.logging import get_exc_desc

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
        result = await send_debug(bot)
    except Exception as exc:
        logger.exception(f"早报测试失败: {get_exc_desc(exc)}")
        await debug_cmd.finish(f"早报测试失败：{get_exc_desc(exc)}")
    await debug_cmd.finish(result)
