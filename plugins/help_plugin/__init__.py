from nonebot import on_message
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Bot
import random
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from ..plugin_manager import is_plugin_enabled

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


# 创建消息处理器，使用精确匹配规则
keywords = ["help", "帮助", "功能"]
help_handler = on_message(rule=exact_match_keywords(keywords), priority=10, block=True)


@help_handler.handle()
async def handle_help(bot: Bot, event: MessageEvent):
    # 将长文本分割成多个段落
    help_segments = [
        "欢迎使用群高性能萝卜子ATRI！",
        
        "ATRI挂载了HarukiBot\n"
        "帮助文档：https://docs.haruki.seiunx.com",

        "ATRI挂载了SakuraBot的部分娱乐功能与Project Sekai相关功能\n"
        "帮助文档：https://help.mikuware.top\n"
        "数据上传链接：http://go.mikuware.top",

        "ATRI还有部分其他功能：\n"
        "1. 表情制作：可通过发送\"pjsk\"或\"arc\"制作相应表情\n"
        "2. 赛博浅草寺：可通过发送\"抽签\"看看你今天的赛博签运\n"
        "3. 今日人品：可通过发送\"jrrp\"查看你今天的人品值（每日一次）\n"
        "4. 表情保存：可通过对动画表情回复\"save/保存\"来获取可保存的图片形式的动画表情\n"
        "5. 复读姬、分析B站视频链接",

        "6. 戳一戳回复功能。可通过戳一戳ATRI来获取一段神秘文字（或图片）。\n可通过“@ATRI 投稿+内容”的方式来增加回复，可通过“@ATRI 查看投稿统计”来查看本群的回复文本以及图片数量\n为了满足可能存在的复制需求，对于长文本转化的图片，可通过回复”转文字“来获取原文~\n"
        "7. （也许可以正常工作的）Bilibili动态推送！目前暂不支持非su增加订阅喵！\n"
        "8. BUAAMSM！私聊发送buaamshelp获取详细帮助文档。特别鸣谢热心群友@吃井不忘挖水人 提供的代码！\n"
        "9.群聊消息统计！来看看今天大家都聊了多少吧~\n"
        "10.PJSK猜歌模块！发送“猜歌帮助”获取更多详细信息\n",

        "关于BUAAMSM插件：\n"
        "该功能由于不可抗力以及技术原因，仅在***私聊***中使用，请添加ATRI好友发送“buaamshelp”查看完整帮助文档。\n"
        "在添加好友时，请在验证信息内填写***本群群号或群名***，ATRI会自动通过好友申请。\n"
        "如果你在***正确填写了验证信息***后，发现ATRI未通过好友申请，可能是插件出问题或是bot出问题，请一段时间后再试一次。若相同问题持续出现，请联系bot主。"
    ]

    # 创建转发消息节点列表
    forward_nodes = []

    # 为每个段落创建消息节点
    for i, segment in enumerate(help_segments):
        node = {
            "type": "node",
            "data": {
                "name": "ATRI帮助文档",
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
        fallback_text = "\n\n".join(help_segments)
        await help_handler.finish(fallback_text)