from nonebot import on_message, logger
from nonebot.rule import Rule, to_me
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from core.access import switch_rule
from utils.logging import get_exc_desc


async def _is_recall_request(bot: Bot, event: MessageEvent) -> bool:
    # 只放行「回复 bot 的消息并说撤回」：rule 太宽会挡住所有 @bot 消息和私聊
    if event.get_plaintext().strip() != "撤回" or event.reply is None:
        return False
    # 校验被回复的消息是否为机器人自己发送的，防止撤回他人消息
    reply_sender_id = event.reply.sender.user_id
    return reply_sender_id is not None and str(reply_sender_id) == bot.self_id


recall = on_message(
    rule=to_me() & Rule(_is_recall_request) & switch_rule("recall:self_recall"),
    priority=10,
    block=True,
)


@recall.handle()
async def handle_recall(bot: Bot, event: MessageEvent):
    # 获取被回复的消息ID（即机器人发送的消息）
    reply_msg_id = event.reply.message_id

    try:
        # 尝试撤回消息
        await bot.delete_msg(message_id=reply_msg_id)
        logger.info(f"消息 {reply_msg_id} 已撤回")
    except Exception as e:
        logger.warning(f"撤回消息失败: {get_exc_desc(e)}")
        await recall.finish("撤回消息失败")
