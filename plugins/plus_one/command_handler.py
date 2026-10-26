from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER

from core.settings import reload_plugin_config
from plugins.plus_one.config import config, save_config
from utils.logging import get_exc_desc

# 配置管理命令
reload_config = on_command("重载复读配置", permission=SUPERUSER, priority=5, block=True)
view_config = on_command("查看复读配置", permission=SUPERUSER, priority=5, block=True)
add_blocked_word = on_command(
    "添加屏蔽词", permission=SUPERUSER, priority=5, block=True
)
remove_blocked_word = on_command(
    "移除屏蔽词", permission=SUPERUSER, priority=5, block=True
)


@reload_config.handle()
async def reload_config_handler(matcher: Matcher):
    """重新加载配置"""
    try:
        reload_plugin_config("plus_one", config)
    except Exception as e:
        logger.exception("重载 plus_one 配置失败")
        await matcher.finish(f"配置无效，继续使用旧配置：{get_exc_desc(e)}")
    await matcher.send("已重新加载复读姬插件配置")


@view_config.handle()
async def view_config_handler(matcher: Matcher):
    """查看当前配置"""
    message = "当前复读姬插件配置:\n\n"
    message += f"屏蔽词数量: {len(config.blocked_words)}\n"
    message += f"屏蔽词列表: {', '.join(sorted(config.blocked_words))}"

    await matcher.send(message)


@add_blocked_word.handle()
async def add_blocked_word_handler(matcher: Matcher, args: Message = CommandArg()):
    """添加屏蔽词"""
    word = args.extract_plain_text().strip()
    if not word:
        await matcher.finish("请提供要添加的屏蔽词")

    if word in config.blocked_words:
        await matcher.finish(f"屏蔽词 '{word}' 已存在")

    config.blocked_words.add(word)
    if save_config():
        await matcher.send(f"已添加屏蔽词: {word}")
    else:
        await matcher.send("添加失败，请检查日志")


@remove_blocked_word.handle()
async def remove_blocked_word_handler(matcher: Matcher, args: Message = CommandArg()):
    """移除屏蔽词"""
    word = args.extract_plain_text().strip()
    if not word:
        await matcher.finish("请提供要移除的屏蔽词")

    if word not in config.blocked_words:
        await matcher.finish(f"屏蔽词 '{word}' 不存在")

    config.blocked_words.remove(word)
    if save_config():
        await matcher.send(f"已移除屏蔽词: {word}")
    else:
        await matcher.send("移除失败，请检查日志")
