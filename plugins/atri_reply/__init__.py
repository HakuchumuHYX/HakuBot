import json
import random
from pathlib import Path
from typing import List

from nonebot import get_driver, on_message, logger
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.rule import Rule
from nonebot.exception import FinishedException
from ..plugin_manager.enable import is_plugin_enabled

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"

# 全局配置
bot_config = {
    "nicknames": [],
    "reply_texts": []
}


def load_config():
    """加载配置文件"""
    global bot_config
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                bot_config["nicknames"] = config_data.get("bot_nicknames", [])
                bot_config["reply_texts"] = config_data.get("reply_texts", [])
            logger.info(
                f"配置文件加载成功，昵称: {bot_config['nicknames']}, 回复文本: {len(bot_config['reply_texts'])} 条")
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {
                "bot_nicknames": ["小助手", "机器人", "bot", "助手"],
                "reply_texts": [
                    "我在呢！有什么需要帮助的吗？",
                    "你好呀！找我有什么事吗？",
                    "我在这里呢~"
                ]
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            bot_config = default_config
            logger.info("创建默认配置文件成功")
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")


def is_calling_bot(event: MessageEvent) -> bool:
    user_id = str(event.user_id)
    if hasattr(event, 'group_id') and event.group_id:
        if not is_plugin_enabled("atri_reply", str(event.group_id), user_id):
            return False
    """检查消息是否刚好在呼叫机器人"""
    message_text = event.get_plaintext().strip()

    # 检查消息是否刚好是昵称之一
    return message_text in bot_config["nicknames"]


# 创建消息处理器
call_reply = on_message(rule=Rule(is_calling_bot), priority=10, block=True)


@call_reply.handle()
async def handle_call_reply(event: MessageEvent):
    """处理呼叫机器人的消息"""
    try:
        # 从回复文本中随机选择一条
        if bot_config["reply_texts"]:
            reply_text = random.choice(bot_config["reply_texts"])
            await call_reply.finish(reply_text)
        else:
            logger.warning("回复文本列表为空，无法回复")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理呼叫回复时出错: {e}")


# 插件启动时加载配置
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    load_config()
    logger.info("简单呼叫回复插件初始化完成")


# 重新加载配置的命令（可选功能）
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

reload_config = on_command("重载配置", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


@reload_config.handle()
async def handle_reload_config():
    """重载配置文件"""
    load_config()
    await reload_config.finish("配置文件重载成功！")