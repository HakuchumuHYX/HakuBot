import re
from typing import List, Tuple
from nonebot import logger
from nonebot.adapters.onebot.v11 import (
    Message, GroupMessageEvent, MessageSegment, Bot
)
from nonebot.rule import Rule

def get_group_id(event) -> int:
    """从事件中获取群号"""
    if hasattr(event, 'group_id'):
        return event.group_id
    return 0

def preprocess_text(text: str) -> str:
    """预处理文本：去除标点符号和空格，转换为小写"""
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower()

def ensure_at_me():
    """确保消息at了机器人"""
    async def _checker(event: GroupMessageEvent) -> bool:
        for segment in event.original_message:
            if segment.type == "at" and segment.data.get("qq") == str(event.self_id):
                return True
        return False
    return Rule(_checker)

async def create_forward_message(bot: Bot, group_id: int, messages: List[Tuple[str, str, str]]) -> List[dict]:
    """
    创建合并转发消息（支持文本和图片base64）
    messages: 列表，元素为 (发送者名称, "text"或"image", 内容或base64)
    """
    try:
        bot_info = await bot.get_login_info()
        bot_uin = bot_info.get("user_id", bot.self_id)
        forward_nodes = []
        for sender_name, msg_type, content in messages:
            if msg_type == "text":
                node_content = MessageSegment.text(content)
            elif msg_type == "image":
                # content 应为 "base64://..." 格式
                node_content = MessageSegment.image(content)
            else:
                node_content = MessageSegment.text(str(content))

            node = {
                "type": "node",
                "data": {
                    "name": sender_name,
                    "uin": str(bot_uin),
                    "content": node_content
                }
            }
            forward_nodes.append(node)
        return forward_nodes
    except Exception as e:
        logger.error(f"创建合并转发消息失败: {e}")
        return [
            {
                "type": "node",
                "data": {
                    "name": "错误",
                    "uin": str(bot.self_id),
                    "content": "合并转发消息创建失败"
                }
            }
        ]

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
