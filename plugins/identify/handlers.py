"""鉴定 / 鉴定你：从 resources 里抽一张图，每人每天固定"""

import random
from datetime import date
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent

from core.access import switch_rule
from utils.onebot.forward import send_forward_msg
from utils.onebot.media import image_segment
from utils.onebot.messages import mentioned_users
from utils.onebot.rules import command_boundary

# 排序后同一个种子在不同进程里抽到同一张
IMAGES = sorted((Path(__file__).parent / "resources").iterdir())

identify = on_command(
    "鉴定",
    rule=command_boundary & switch_rule("identify"),
    permission=GROUP,
    priority=5,
    block=True,
)
identify_other = on_command(
    "鉴定你",
    rule=command_boundary & switch_rule("identify"),
    permission=GROUP,
    priority=5,
    block=True,
)


def _sender_name(event: GroupMessageEvent) -> str:
    return event.sender.card or event.sender.nickname


async def _send(bot: Bot, event: GroupMessageEvent, target_id: int, text: str):
    # str 种子走 sha512，和 PYTHONHASHSEED 无关，重启后当天结果不变
    seed = f"{date.today()}:{event.group_id}:{target_id}"
    image = random.Random(seed).choice(IMAGES)
    await send_forward_msg(bot, event, items=[text, image_segment(image)])


@identify.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    await _send(bot, event, event.user_id, f"呀吼！@{_sender_name(event)}，经鉴定你是")


@identify_other.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    targets = mentioned_users(event.message)
    if not targets:
        await identify_other.finish("请@一个要鉴定的人喵~\n用法：鉴定你 @某人")
    target_id = targets[0]
    if target_id == int(bot.self_id):
        await identify_other.finish("我不能鉴定我自己喵~")
    if target_id == event.user_id:
        await _send(bot, event, target_id, f"呀吼！@{_sender_name(event)}，经鉴定你是")
        return

    target = await bot.get_group_member_info(group_id=event.group_id, user_id=target_id)
    target_name = target["card"] or target["nickname"]
    text = f"呀吼！@{_sender_name(event)} 对 @{target_name} 的鉴定结果：\n@{target_name} 是"
    await _send(bot, event, target_id, text)
