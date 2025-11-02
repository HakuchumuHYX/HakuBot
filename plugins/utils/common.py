from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.rule import Rule
import asyncio
from inspect import iscoroutinefunction

def create_exact_command_rule(command: str, aliases: set = None, extra_rule=None):
    valid_commands = {command}
    if aliases:
        valid_commands.update(aliases)

    async def _rule(event: GroupMessageEvent) -> bool:
        # 检查消息是否精确匹配命令
        msg = event.message.extract_plain_text().strip()
        if msg not in valid_commands:
            return False

        # 如果有额外规则，检查额外规则
        if extra_rule:
            if iscoroutinefunction(extra_rule):
                return await extra_rule(event)
            else:
                return extra_rule(event)

        return True

    return _rule