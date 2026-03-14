# plugins/buaa_msm/handlers/msr.py
"""
MSR 命令入口（私聊/群聊）：
- 私聊：buaamsr
- 群聊：提示仅私聊可用
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, PrivateMessageEvent
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.rule import is_type

from ..services.msr_service import run_msr
from ..services.processing_guard import is_processing, set_processing


msr_cmd = on_command(
    "buaamsr",
    rule=is_type(PrivateMessageEvent),
    priority=5,
    block=True,
)


@msr_cmd.handle()
async def handle_msr_command(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    user_id = str(event.user_id)

    if await is_processing(user_id):
        await msr_cmd.finish("您的请求正在处理中，请稍候...")
        return

    await set_processing(user_id, True)
    try:
        await msr_cmd.send("正在生成分析结果，请稍候...")
        await run_msr(bot=bot, user_id=user_id, event_user_id=event.user_id, send_func=msr_cmd.send)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"处理 buaamsr 命令失败: {e}")
        await msr_cmd.finish(f"处理失败: {str(e)}")
    finally:
        await set_processing(user_id, False)


# 群聊提示
group_msr_cmd = on_command("buaamsr", rule=is_type(GroupMessageEvent), priority=5, block=True)


@group_msr_cmd.handle()
async def handle_group_msr(bot: Bot, event: GroupMessageEvent):
    await group_msr_cmd.finish("该指令仅在私聊中可用")
