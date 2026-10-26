from nonebot.adapters import Message
from nonebot.params import CommandArg, CommandWhitespace


async def command_boundary(
    arg: Message = CommandArg(), whitespace: str | None = CommandWhitespace()
) -> bool:
    """命令后面必须是空白、非文字段（@、图片等）或消息结束。

    COMMAND_START 含空字符串且命令按最长前缀匹配，不加这条「chatgpt好用吗」会触发 chat。
    不用 NoneBot 的 force_whitespace：它把「鉴定你@某人」这种 @ 前没空格的写法判为不匹配。
    """
    return whitespace is not None or not arg or not arg[0].is_text()
