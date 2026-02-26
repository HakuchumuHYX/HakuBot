"""
帮助命令：hltv帮助 / hltv / hltvhelp
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

from ..permissions import is_group_enabled
from ..render import render_help


hltv_help = on_command("hltv帮助", aliases={"hltvhelp"}, priority=5, block=True)


@hltv_help.handle()
async def handle_hltv_help(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    if not is_group_enabled(group_id):
        return

    sections = [
        {
            "title": "赛事相关",
            "note": "订阅需管理员",
            "commands": [
                {
                    "name": "event列表",
                    "args": "",
                    "aliases": ["赛事列表", "events"],
                    "desc": "查看近期大型赛事列表（包含进行中/未开始），并标注你已订阅的赛事。",
                    "admin_only": False,
                    "superuser_only": False,
                },
                {
                    "name": "event订阅",
                    "args": "[ID]",
                    "aliases": ["订阅赛事", "subscribe"],
                    "desc": "订阅指定赛事（需要管理员权限）；赛事进行中或开赛前 24h 会自动开启轮询推送。",
                    "admin_only": True,
                    "superuser_only": False,
                },
                {
                    "name": "event取消订阅",
                    "args": "[ID]",
                    "aliases": ["取消订阅赛事", "unsubscribe"],
                    "desc": "取消订阅指定赛事（需要管理员权限），不再接收该赛事的提醒/结果推送。",
                    "admin_only": True,
                    "superuser_only": False,
                },
                {
                    "name": "我的订阅",
                    "args": "",
                    "aliases": ["订阅列表", "mysub"],
                    "desc": "查看当前群已订阅的赛事及赛事时间范围。",
                    "admin_only": False,
                    "superuser_only": False,
                },
            ],
        },
        {
            "title": "比赛相关",
            "note": "查询类命令",
            "commands": [
                {
                    "name": "matches列表",
                    "args": "",
                    "aliases": ["比赛列表", "matches"],
                    "desc": "查看订阅赛事的对阵信息与开赛时间（图片列表）。",
                    "admin_only": False,
                    "superuser_only": False,
                },
                {
                    "name": "results列表",
                    "args": "",
                    "aliases": ["结果列表", "results"],
                    "desc": "查看订阅赛事的最近比赛结果（图片列表）。",
                    "admin_only": False,
                    "superuser_only": False,
                },
                {
                    "name": "stats",
                    "args": "[match_id]",
                    "aliases": ["比赛数据", "数据"],
                    "desc": "不带参数：获取订阅赛事的最新一场比赛数据；带 match_id：查看指定比赛的详细数据（图片）。",
                    "admin_only": False,
                    "superuser_only": False,
                },
            ],
        },
        {
            "title": "管理命令",
            "note": "仅群主/管理员",
            "commands": [
                {
                    "name": "hltv开启",
                    "args": "",
                    "aliases": ["hltv启用"],
                    "desc": "在本群启用 HLTV 功能（需要管理员权限）；未开启时所有命令会被忽略。",
                    "admin_only": True,
                    "superuser_only": False,
                },
                {
                    "name": "hltv关闭",
                    "args": "",
                    "aliases": ["hltv禁用"],
                    "desc": "在本群禁用 HLTV 功能（需要管理员权限），停止响应命令与推送。",
                    "admin_only": True,
                    "superuser_only": False,
                },
            ],
        },
        {
            "title": "调试命令",
            "note": "仅 bot 超级用户",
            "commands": [
                {
                    "name": "hltv_check",
                    "args": "",
                    "aliases": [],
                    "desc": "（调试/超管）查看即将开始的比赛列表与提醒去重状态。",
                    "admin_only": False,
                    "superuser_only": True,
                },
                {
                    "name": "hltv_trigger",
                    "args": "",
                    "aliases": [],
                    "desc": "（调试/超管）手动执行一次定时任务检查，用于排查推送逻辑。",
                    "admin_only": False,
                    "superuser_only": True,
                },
            ],
        },
        {
            "title": "帮助",
            "note": "查看本页",
            "commands": [
                {
                    "name": "hltv帮助",
                    "args": "",
                    "aliases": ["hltvhelp"],
                    "desc": "显示本帮助页面（图片形式），包含所有命令说明与权限标记。",
                    "admin_only": False,
                    "superuser_only": False,
                }
            ],
        },
    ]

    img = await render_help(sections)
    await hltv_help.finish(MessageSegment.image(img))
