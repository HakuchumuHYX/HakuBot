import re
from typing import List, Tuple, Union
from nonebot import logger
from nonebot.adapters.onebot.v11 import Message, GroupMessageEvent, MessageSegment, Bot
from nonebot.rule import Rule
from utils.onebot.media import image_segment
from utils.onebot.forward import ForwardItem


def get_group_id(event) -> int:
    """从事件中获取群号"""
    raw_group_id = getattr(event, "group_id", 0)
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        return 0
    return group_id if group_id > 0 else 0


def preprocess_text(text: str) -> str:
    """预处理文本：去除标点符号和空格，转换为小写"""
    text = re.sub(r"[^\w\s]", "", text)
    return text.lower()


def ensure_at_me():
    """确保消息at了机器人"""

    async def _checker(event: GroupMessageEvent) -> bool:
        for segment in event.original_message:
            if segment.type == "at" and segment.data.get("qq") == str(event.self_id):
                return True
        return False

    return Rule(_checker)


async def create_forward_message(
    bot: Bot,
    group_id: int,
    messages: List[Tuple[str, str, Union[str, MessageSegment]]],
) -> List[ForwardItem]:
    """
    创建合并转发条目（支持文本和共享本地图片）
    messages: 列表，元素为 (发送者名称, "text"或"image", 内容或图片段)
    """
    try:
        forward_items = []
        for sender_name, msg_type, content in messages:
            if msg_type == "text":
                node_content = MessageSegment.text(str(content))
            elif msg_type == "image":
                node_content = (
                    content
                    if isinstance(content, MessageSegment)
                    else image_segment(content)
                )
            else:
                node_content = MessageSegment.text(str(content))
            forward_items.append(
                ForwardItem(node_content, name=sender_name, uin=bot.self_id)
            )
        return forward_items
    except Exception as e:
        logger.error(f"创建合并转发消息失败: {e}")
        return [ForwardItem("合并转发消息创建失败", name="错误", uin=bot.self_id)]


def extract_image_data(message: Message) -> Tuple[bool, list]:
    """提取消息中的图片数据"""
    images = []
    for segment in message:
        if segment.type == "image":
            image_url = segment.data.get("url", "")
            images.append(("image", image_url, segment))
        elif segment.type == "face":
            face_id = segment.data.get("id", "")
            images.append(("face", face_id, segment))
    return len(images) > 0, images
