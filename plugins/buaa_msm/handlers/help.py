# plugins/buaa_msm/handlers/help.py
"""
帮助命令入口（私聊/群聊）。

迁移自 plugins/buaa_msm/help.py（保持文本不变）。
"""

from __future__ import annotations

from typing import List

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent
from nonebot.log import logger
from nonebot.rule import is_type

# 群聊帮助文本
GROUP_HELP_TEXT = """该功能由于技术原因，仅在**私聊**中使用，请**添加ATRI好友**发送"buaamshelp"查看完整帮助文档。
在添加好友时，请在验证信息内填写**本群群号**，ATRI会自动通过好友申请。
如果你发现在**正确填写了验证信息**的情况下，好友申请未通过，可通过"@ATRI send+文本"的方式联系bot主。"""

# 私聊帮助文本
PRIVATE_HELP_TEXT = """欢迎使用BUAAMSM插件，更适合百航宝宝体质的MSM插件
该插件只在私聊中启用，且仅供学习交流使用。
发送"buaa绑定+一串文本"来绑定你的QQ号。这里的一串文本可以是任意文本，只要长度适中且不包含特殊字符即可。
发送"buaa上传文件"，然后将你的mysekai包体直接通过文件传输的方式传给ATRI，ATRI会自动解密并返回分析结果（统计图+位置图）。
请注意：烤森每天五点和十七点更新，所以会定时清理上传的mysekai数据。
如果你发现自己上传错误，也可以随时使用"buaa上传文件"重新上传，插件会自动检测最新上传的文件。
如果你想基于已上传的数据重新生成分析结果，可以发送"buaamsr"。
最后：如果你发现任何问题，请联系bot主。Have fun!"""


async def create_forward_nodes(text: str, bot_name: str, bot_uin: str) -> List[MessageSegment]:
    """将长文本按行分割，创建合并转发节点"""
    nodes = []
    lines = text.strip().split("\n")

    for line in lines:
        if not line.strip():
            continue
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(bot_uin),
                nickname=bot_name,
                content=Message(line.strip()),
            )
        )

    return nodes


private_help_cmd = on_command(
    "buaamshelp",
    aliases={"mshelp"},
    rule=is_type(PrivateMessageEvent),
    priority=5,
    block=True,
)


@private_help_cmd.handle()
async def handle_private_help(bot: Bot, event: PrivateMessageEvent):
    """处理私聊help命令"""
    try:
        bot_info = await bot.get_login_info()
        bot_uin = bot_info.get("user_id")
        bot_name = bot_info.get("nickname", "ATRI")

        nodes = await create_forward_nodes(PRIVATE_HELP_TEXT, bot_name, str(bot_uin))

        await bot.send_private_forward_msg(user_id=event.user_id, messages=nodes)
    except Exception as e:
        logger.error(f"发送私聊帮助失败: {e}")
        await private_help_cmd.send(PRIVATE_HELP_TEXT)

    await private_help_cmd.finish()


group_help_cmd = on_command(
    "buaamshelp",
    aliases={"mshelp"},
    rule=is_type(GroupMessageEvent),
    priority=5,
    block=True,
)


@group_help_cmd.handle()
async def handle_group_help(bot: Bot, event: GroupMessageEvent):
    """处理群聊help命令"""
    try:
        bot_info = await bot.get_login_info()
        bot_uin = bot_info.get("user_id")
        bot_name = bot_info.get("nickname", "ATRI")

        nodes = await create_forward_nodes(GROUP_HELP_TEXT, bot_name, str(bot_uin))

        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    except Exception as e:
        logger.error(f"发送群聊帮助失败: {e}")
        await group_help_cmd.send(GROUP_HELP_TEXT)

    await group_help_cmd.finish()


logger.success("帮助 handlers 已加载成功！")
