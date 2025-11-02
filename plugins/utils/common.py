from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.rule import Rule
import asyncio
from inspect import iscoroutinefunction
import re


def create_exact_command_rule(command: str, aliases: set = None, extra_rule=None):
    """
    创建精确匹配命令的规则，支持带参数的命令

    Args:
        command: 主命令名
        aliases: 命令别名集合
        extra_rule: 额外的规则函数
    """
    # 构建所有有效命令的集合
    valid_commands = {command}
    if aliases:
        valid_commands.update(aliases)

    # 构建正则表达式模式，匹配命令后跟空格或结束
    pattern = re.compile(rf'^({"|".join(re.escape(cmd) for cmd in valid_commands)})(\s+|$)')

    async def _rule(event: GroupMessageEvent) -> bool:
        # 获取纯文本消息
        msg = event.message.extract_plain_text().strip()

        # 检查消息是否以有效命令开头，后面跟空格或直接结束
        if not pattern.match(msg):
            return False

        # 如果有额外规则，检查额外规则
        if extra_rule:
            return await extra_rule(event)

        return True

    return _rule