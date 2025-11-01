import nonebot
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, GroupMessageEvent
from nonebot.log import logger

# 帮助命令
help_cmd = on_command("buaamshelp", priority=5, block=True)

# 群聊帮助文本
GROUP_HELP_TEXT = """该功能由于不可抗力以及技术原因，仅在***私聊***中使用，请添加ATRI好友发送“buaamshelp”查看完整帮助文档。
在添加好友时，请在验证信息内填写***本群群号或群名***，ATRI会自动通过好友申请。"""

# 私聊帮助文本
PRIVATE_HELP_TEXT = """欢迎使用BUAAMSM插件，更适合百航宝宝体质的MSM插件（）
特别鸣谢：热心群友@吃井不忘挖水人 提供的代码援助！
该插件只在私聊中启用，且仅供学习交流使用。
发送“buaa绑定+一串文本”来绑定你的QQ号。这里的一串文本可以是任意文本，只要长度适中且不包含特殊字符即可。
发送“buaa上传文件”，然后将你的mysekai包体直接通过文件传输的方式传给ATRI。
发送“buaamsm”，查看神秘小图片。
请注意：烤森每天五点和十七点更新，所以会定时清理上传的mysekai数据。
如果你发现自己上传错误，也可以随时使用"buaa上传文件"重新上传，插件会自动检测最新上传的文件。
抓包教程：https://arthur-stat.github.io/2025/07/23/pjskcapture/。注意：请***不要***随意传播该教程。如果你看不懂，也可以参考sakurabot的上传数据相关的帮助文档，原理是一样的。
最后：如果你发现任何问题，请联系bot主。Have fun!"""

@help_cmd.handle()
async def handle_private_help(bot: Bot, event: PrivateMessageEvent):
    """处理私聊help命令"""
    await help_cmd.finish(PRIVATE_HELP_TEXT)

@help_cmd.handle()
async def handle_group_help(bot: Bot, event: GroupMessageEvent):
    """处理群聊help命令"""
    await help_cmd.finish(GROUP_HELP_TEXT)

# 插件加载成功提示
logger.success("帮助模块加载成功！")