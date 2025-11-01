from nonebot import on_message
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Bot
import random
from nonebot.adapters.onebot.v11 import GroupMessageEvent
import json
from pathlib import Path
from nonebot.log import logger

from ..plugin_manager import is_plugin_enabled

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"


def load_help_config():
    """加载帮助文档配置"""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                logger.info("成功加载帮助文档配置")
                return config_data
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {
                "help_segments": [
                    "test_text1",
                    "test_text2"
                ],
                "keywords": ["help", "帮助", "功能"],
                "bot_name": "ATRI帮助文档"
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            logger.warning("配置文件不存在，已创建默认配置文件")
            return default_config
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}，使用默认配置")
        # 返回默认配置
        return {
            "help_segments": ["默认帮助文档，请检查配置文件"],
            "keywords": ["help", "帮助", "功能"],
            "bot_name": "ATRI帮助文档"
        }


# 创建精确匹配规则，支持多个关键词
def exact_match_keywords(keywords: list) -> Rule:
    async def _exact_match(event: MessageEvent) -> bool:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            if not is_plugin_enabled("help_plugin", group_id):
                return False
        # 获取纯文本消息并去除首尾空格
        msg = event.get_plaintext().strip()
        # 精确匹配关键词列表中的任何一个
        return msg in keywords

    return Rule(_exact_match)


# 加载配置
config = load_help_config()
keywords = config.get("keywords", ["help", "帮助", "功能"])
bot_name = config.get("bot_name", "ATRI帮助文档")

# 创建消息处理器，使用精确匹配规则
help_handler = on_message(rule=exact_match_keywords(keywords), priority=10, block=True)


@help_handler.handle()
async def handle_help(bot: Bot, event: MessageEvent):
    # 从配置中获取帮助文档段落
    help_segments = config.get("help_segments", [])

    if not help_segments:
        await help_handler.finish("帮助文档配置为空，请联系管理员")
        return

    # 创建转发消息节点列表
    forward_nodes = []

    # 为每个段落创建消息节点
    for i, segment in enumerate(help_segments):
        node = {
            "type": "node",
            "data": {
                "name": bot_name,
                "uin": bot.self_id,  # 使用机器人自己的QQ号
                "content": segment
            }
        }
        forward_nodes.append(node)

    # 发送合并转发消息
    try:
        if hasattr(event, 'group_id') and event.group_id:
            # 群聊中发送
            await bot.send_group_forward_msg(
                group_id=event.group_id,
                messages=forward_nodes
            )
        else:
            # 私聊中发送
            await bot.send_private_forward_msg(
                user_id=event.user_id,
                messages=forward_nodes
            )
    except Exception as e:
        # 如果转发消息发送失败，回退到普通消息
        logger.error(f"发送合并转发消息失败: {e}，回退到普通消息")
        fallback_text = "\n\n".join(help_segments)
        await help_handler.finish(fallback_text)