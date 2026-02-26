from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.log import logger

from .config import config

# 配置管理命令
reload_config = on_command("重载复读配置", permission=SUPERUSER, priority=1, block=True)
view_config = on_command("查看复读配置", permission=SUPERUSER, priority=1, block=True)
add_blocked_word = on_command("添加屏蔽词", permission=SUPERUSER, priority=1, block=True)
remove_blocked_word = on_command("移除屏蔽词", permission=SUPERUSER, priority=1, block=True)
add_black_list = on_command("添加黑名单", permission=SUPERUSER, priority=1, block=True)
remove_black_list = on_command("移除黑名单", permission=SUPERUSER, priority=1, block=True)


@reload_config.handle()
async def reload_config_handler(matcher: Matcher):
    """重新加载配置"""
    config.load_config()
    await matcher.send("已重新加载复读姬插件配置")


@view_config.handle()
async def view_config_handler(matcher: Matcher):
    """查看当前配置"""
    message = "当前复读姬插件配置:\n\n"
    message += f"响应优先级: {config.plus_one_priority}\n"
    message += f"黑名单群组: {config.plus_one_black_list}\n"
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
    if config.save_config():
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
    if config.save_config():
        await matcher.send(f"已移除屏蔽词: {word}")
    else:
        await matcher.send("移除失败，请检查日志")


@add_black_list.handle()
async def add_black_list_handler(matcher: Matcher, args: Message = CommandArg()):
    """添加黑名单群组"""
    group_id = args.extract_plain_text().strip()
    if not group_id:
        await matcher.finish("请提供要添加到黑名单的群号")

    if not group_id.isdigit():
        await matcher.finish("群号必须为数字")

    if group_id in config.plus_one_black_list:
        await matcher.finish(f"群组 {group_id} 已在黑名单中")

    config.plus_one_black_list.append(group_id)
    if config.save_config():
        await matcher.send(f"已添加群组 {group_id} 到黑名单")
    else:
        await matcher.send("添加失败，请检查日志")


@remove_black_list.handle()
async def remove_black_list_handler(matcher: Matcher, args: Message = CommandArg()):
    """移除黑名单群组"""
    group_id = args.extract_plain_text().strip()
    if not group_id:
        await matcher.finish("请提供要从黑名单移除的群号")

    if group_id not in config.plus_one_black_list:
        await matcher.finish(f"群组 {group_id} 不在黑名单中")

    config.plus_one_black_list.remove(group_id)
    if config.save_config():
        await matcher.send(f"已从黑名单移除群组 {group_id}")
    else:
        await matcher.send("移除失败，请检查日志")