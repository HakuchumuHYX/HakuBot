from nonebot.plugin import on_message
from nonebot.rule import regex
from nonebot.adapters import Event, Message, Bot
from nonebot_plugin_session import extract_session, SessionIdType

from .config import config
from ..plugin_manager.enable import is_plugin_enabled

plus = on_message(rule=regex(""), priority=config.plus_one_priority, block=False)
msg_dict = {}


def is_equal(msg1: Message, msg2: Message):
    """判断是否相等"""
    if len(msg1) == len(msg2) == 1 and msg1[0].type == msg2[0].type == "image":
        if msg1[0].data["file_size"] == msg2[0].data["file_size"]:
            return True
    if msg1 == msg2:
        return True


def contains_blocked_words(text: str) -> bool:
    """检查是否包含屏蔽词"""
    # 从配置中获取屏蔽词集合
    blocked_words = config.blocked_words

    # 如果没有设置屏蔽词，直接返回 False
    if not blocked_words:
        return False

    # 检查文本是否包含任何屏蔽词
    for word in blocked_words:
        if word in text:
            return True

    return False

def extract_text_from_message(msg: Message) -> str:
    """从消息对象中提取文本内容"""
    text_parts = []
    for segment in msg:
        if segment.type == 'text':
            text_parts.append(segment.data.get('text', ''))
    return ''.join(text_parts)


@plus.handle()
async def plush_handler(bot: Bot, event: Event):
    global msg_dict

    session = extract_session(bot, event)
    group_id = session.get_id(SessionIdType.GROUP).split("_")[-1]

    # 检查插件是否启用
    if not is_plugin_enabled("plus_one", group_id):
        return

    if group_id in config.plus_one_black_list:
        return

    # 获取当前信息
    msg = event.get_message()

    message_text = extract_text_from_message(msg)
    if contains_blocked_words(message_text):
        # 如果包含屏蔽词，不进行+1处理
        return

    # 获取群聊记录
    text_list = msg_dict.get(group_id, None)
    if not text_list:
        text_list = []
        msg_dict[group_id] = text_list

    try:
        if not is_equal(text_list[-1], msg):
            text_list = []
            msg_dict[group_id] = text_list
    except IndexError:
        pass

    text_list.append(msg)

    if len(text_list) == 2:
        await plus.send(msg)