import emoji
from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import (
    GROUP,
    ActionFailed,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.typing import T_State

from core.access import switch_rule
from utils.logging import get_exc_desc

# 一次发上百个 emoji 就是上百次接口调用，容易触发风控
MAX_EMOJIS = 20


def _parse(message: Message) -> list[str]:
    """「贴」后面只有 QQ 表情、emoji 和空白时，返回去重后的 emoji_id；混了别的就返回空列表。"""
    if not message[0].is_text():
        return []
    head = message[0].data["text"]
    if not head.startswith("贴"):
        return []
    ids = []
    for seg in [MessageSegment.text(head[1:]), *message[1:]]:
        if seg.type == "face":
            ids.append(str(seg.data["id"]))
        elif seg.type == "text":
            text = seg.data["text"]
            # 混了文字（「贴贴」「贴吧」）说明这句本来就不是贴表情
            if emoji.replace_emoji(text, "").strip():
                return []
            for item in emoji.emoji_list(text):
                char = item["emoji"].replace("️", "")
                # 接口只收一个码点，肤色、国旗、组合 emoji 贴不了，跳过这一个
                if len(char) != 1:
                    continue
                ids.append(str(ord(char)))
        else:
            return []
    return list(dict.fromkeys(ids))[:MAX_EMOJIS]


async def _emoji_rule(event: GroupMessageEvent, state: T_State) -> bool:
    # 适配器取被回复的消息失败时 reply 段会留在开头，_parse 不认，不会误贴到命令自己身上
    ids = _parse(event.message)
    if not ids:
        return False
    state["emoji_ids"] = ids
    return True


emoji_like = on_message(
    rule=_emoji_rule & switch_rule("emoji_like"),
    permission=GROUP,
    priority=10,
    block=True,
)


@emoji_like.handle()
async def handle_emoji_like(bot: Bot, event: GroupMessageEvent, state: T_State):
    # 回复段和自动带的 @ 已被适配器去掉，event.reply 就是被回复的那条
    target = event.reply.message_id if event.reply else event.message_id
    for emoji_id in state["emoji_ids"]:
        try:
            await bot.call_api(
                "set_msg_emoji_like", message_id=target, emoji_id=emoji_id, set=True
            )
        except ActionFailed as e:
            logger.warning(f"贴表情 {emoji_id} 到消息 {target} 失败: {get_exc_desc(e)}")
