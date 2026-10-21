import random

from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.rule import Rule
from nonebot.exception import FinishedException
from core.access import is_plugin_enabled
from core.settings import ConfigModel, load_config
from utils.logging import get_exc_desc


class Config(ConfigModel):
    bot_nicknames: list[str]
    reply_texts: list[str]


plugin_config = load_config("atri_reply", Config)


def is_calling_bot(event: MessageEvent) -> bool:
    user_id = str(event.user_id)
    if hasattr(event, "group_id") and event.group_id:
        if not is_plugin_enabled("atri_reply", str(event.group_id), user_id):
            return False
    """检查消息是否刚好在呼叫机器人"""
    message_text = event.get_plaintext().strip()

    # 检查消息是否刚好是昵称之一
    return message_text in plugin_config.bot_nicknames


# 创建消息处理器
call_reply = on_message(rule=Rule(is_calling_bot), priority=10, block=True)


@call_reply.handle()
async def handle_call_reply(event: MessageEvent):
    """处理呼叫机器人的消息"""
    try:
        # 从回复文本中随机选择一条
        if plugin_config.reply_texts:
            reply_text = random.choice(plugin_config.reply_texts)
            await call_reply.finish(reply_text)
        else:
            logger.warning("回复文本列表为空，无法回复")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.exception(f"处理呼叫回复时出错: {e}")


# 重新加载配置的命令（可选功能）
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

reload_config = on_command(
    "重载配置", permission=SUPERUSER, rule=to_me(), priority=5, block=True
)


@reload_config.handle()
async def handle_reload_config():
    """重载配置文件；新配置无效时保留旧配置"""
    global plugin_config
    try:
        plugin_config = load_config("atri_reply", Config)
    except Exception as e:
        logger.exception("重载 atri_reply 配置失败")
        await reload_config.finish(f"配置无效，继续使用旧配置：{get_exc_desc(e)}")
    await reload_config.finish("配置文件重载成功！")
