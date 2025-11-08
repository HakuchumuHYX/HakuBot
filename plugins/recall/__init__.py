from nonebot import on_message
from nonebot.adapters import Event
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.log import logger

recall = on_message(rule=to_me(), priority=10)


@recall.handle()
async def handle_recall(bot: Bot, event: Event):
    # 检查消息内容是否为"撤回"
    if event.get_plaintext().strip() != "撤回":
        return

    # 获取原始事件
    if not isinstance(event, MessageEvent):
        return

    # 检查是否是回复消息
    if not hasattr(event, 'reply') or event.reply is None:
        await recall.finish()
        return

    # 获取被回复的消息ID（即机器人发送的消息）
    reply_msg_id = event.reply.message_id

    try:
        # 尝试撤回消息
        await bot.delete_msg(message_id=reply_msg_id)
        logger.info(f"消息 {reply_msg_id} 已撤回")
    except Exception as e:
        logger.error(f"撤回消息失败: {e}")
        await recall.finish("撤回消息失败")

