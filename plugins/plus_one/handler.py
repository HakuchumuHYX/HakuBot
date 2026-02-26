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
        # 优先使用 file_size 比较
        file_size1 = msg1[0].data.get("file_size")
        file_size2 = msg2[0].data.get("file_size")
        if file_size1 is not None and file_size2 is not None:
            return file_size1 == file_size2
        
        # 回退到 file 字段比较
        file1 = msg1[0].data.get("file")
        file2 = msg2[0].data.get("file")
        if file1 is not None and file2 is not None:
            return file1 == file2
        
        # 无法比较则认为不相等
        return False
    
    return msg1 == msg2


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
    if not is_plugin_enabled("plus_one", group_id, "0"):
        return

    if group_id in config.plus_one_black_list:
        return

    # 如果是机器人自己发的消息（虽然通常 on_message 不会触发，但以防万一有 echo）
    if event.get_user_id() == bot.self_id:
        msg_dict[group_id] = []
        return

    # 获取当前信息
    msg = event.get_message()

    message_text = extract_text_from_message(msg)
    if contains_blocked_words(message_text):
        # 如果包含屏蔽词，清空记录并返回
        msg_dict[group_id] = []
        return

    # 获取群聊记录
    text_list = msg_dict.get(group_id, [])

    # 检查是否与上一条消息相同
    if text_list:
        if not is_equal(text_list[-1], msg):
            # 如果不相同，重置列表
            text_list = []
    
    # 将当前消息加入（此时 text_list 要么是空的，要么包含之前的相同消息）
    text_list.append(msg)
    msg_dict[group_id] = text_list

    if len(text_list) == 2:
        await plus.send(msg)
