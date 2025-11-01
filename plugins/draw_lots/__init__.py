from nonebot import on_command
from nonebot.rule import to_me
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import Bot, Event, Message, GroupMessageEvent
from nonebot.params import Arg, CommandArg, ArgPlainText
from .message import message_sign

import random
import time
import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any

# 新增：导入管理模块
from ..plugin_manager import is_plugin_enabled

# 新增：数据存储路径
DATA_FILE = Path("data/draw_lots/records.json")
DATA_FILE.parent.mkdir(parents=True, exist_ok=True)


# 新增：加载抽签记录
def load_records() -> Dict[str, Dict[str, Any]]:
    """加载抽签记录"""
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


# 新增：保存抽签记录
def save_records(records: Dict[str, Dict[str, Any]]):
    """保存抽签记录"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# 新增：检查今天是否已经抽过签
def has_drawn_today(user_id: str) -> bool:
    """检查用户今天是否已经抽过签"""
    records = load_records()
    today = str(date.today())

    if user_id in records and records[user_id].get("date") == today:
        return True
    return False


# 新增：获取用户今天的签文
def get_today_sign(user_id: str) -> str:
    """获取用户今天的签文"""
    records = load_records()
    return records.get(user_id, {}).get("sign", "")


# 新增：保存用户今天的签文
def save_today_sign(user_id: str, sign: str):
    """保存用户今天的签文"""
    records = load_records()
    today = str(date.today())

    records[user_id] = {
        "date": today,
        "sign": sign
    }
    save_records(records)


command = on_command('抽签', priority=6)


@command.handle()
async def lq_(bot: Bot, event: Event):
    # 新增：检查插件是否启用（群聊中）
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("draw_lots", str(event.group_id)):
            await command.finish("抽签功能当前已被禁用")

    user_id = str(event.user_id)

    # 新增：检查今天是否已经抽过
    if has_drawn_today(user_id):
        sign = get_today_sign(user_id)
        if sign == "空签":
            # 构建空签的转发消息
            forward_msg = [
                {
                    "type": "node",
                    "data": {
                        "name": "赛博浅草寺",
                        "uin": bot.self_id,
                        "content": f"今天您已经抽过了\n您今天抽到的是：{sign}"
                    }
                }
            ]
        else:
            # 构建已抽签的转发消息
            forward_msg = [
                {
                    "type": "node",
                    "data": {
                        "name": "赛博浅草寺",
                        "uin": bot.self_id,
                        "content": f"今天您已经抽过了\n您今天的签文是：\n{sign}"
                    }
                }
            ]

        # 发送合并转发消息
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=forward_msg)
        else:
            await bot.send_private_forward_msg(user_id=event.user_id, messages=forward_msg)
        await command.finish()

    # 直接进行抽签，不再询问确认
    a = random.randint(0, 100)
    if a >= 5:
        pd = True
        sign = random.choice(message_sign)
        # 保存签文
        save_today_sign(user_id, sign)

        # 构建正签的转发消息
        forward_msg = [
            {
                "type": "node",
                "data": {
                    "name": "赛博浅草寺",
                    "uin": bot.self_id,
                    "content": "抽到了正签"
                }
            },
            {
                "type": "node",
                "data": {
                    "name": "签文",
                    "uin": bot.self_id,
                    "content": sign
                }
            }
        ]

        # 发送合并转发消息
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=forward_msg)
        else:
            await bot.send_private_forward_msg(user_id=event.user_id, messages=forward_msg)
        await command.finish()
    else:
        # 保存空签记录
        save_today_sign(user_id, "空签")
        pd = False

        # 构建空签的转发消息
        forward_msg = [
            {
                "type": "node",
                "data": {
                    "name": "赛博浅草寺",
                    "uin": bot.self_id,
                    "content": "是空签呢"
                }
            }
        ]

        # 发送合并转发消息
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=forward_msg)
        else:
            await bot.send_private_forward_msg(user_id=event.user_id, messages=forward_msg)
        await command.finish()