"""
groupmate_waifu/yinpa.py
透群友功能：透群友、涩涩记录等
"""

import random

from nonebot.plugin.on import on_command, on_message
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageSegment,
)

# 本地模块导入
from .utils import get_message_at, user_img, text_to_png, bbcode_to_png
from .rules import check_plugin_enabled, check_yinpa_enabled, is_yinpa_enabled
from .data_manager import (
    # 配置
    yinpa_HE, yinpa_CP, last_sent_time_filter,
    # 数据字典
    record_CP, record_yinpa1, record_yinpa2, protect_list,
    # 保存函数
    save_record_yinpa1, save_record_yinpa2,
)

# 外部模块导入
from ..utils.common import create_exact_command_rule


# ============================================================
# 透群友核心功能
# ============================================================

async def yinpa_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """
    透群友命令的匹配规则
    只做基本的格式匹配和权限检查，不处理业务逻辑
    """
    # 检查功能是否启用
    if not is_yinpa_enabled(str(event.group_id), str(event.user_id)):
        return False
    
    # 检查消息格式
    msg = event.message.extract_plain_text()
    at_list = get_message_at(event.message)
    
    if msg != "透群友" and not (msg.startswith("透群友") and at_list):
        return False
    
    # 检查发送者是否被保护
    group_id = event.group_id
    user_id = event.user_id
    protect_set = protect_list.get(group_id, set())
    
    if user_id in protect_set:
        return False
    
    # 将解析结果存入 state
    at = at_list[0] if at_list else None
    state["at_target"] = at
    return True


yinpa = on_message(rule=yinpa_rule, priority=10, block=True)


@yinpa.handle()
async def handle_yinpa(bot: Bot, event: GroupMessageEvent, state: T_State):
    """透群友核心处理逻辑"""
    group_id = event.group_id
    user_id = event.user_id
    at = state.get("at_target")
    
    protect_set = protect_list.get(group_id, set())
    tips = "你的涩涩对象是、"
    yinpa_id = None
    
    if at:
        # --- 指定目标逻辑 ---
        
        # 检查目标是否被保护
        if at in protect_set:
            await yinpa.finish("对方受到保护，不可以涩涩！", at_sender=True)
        
        # 检查是否 @ 自己
        if at == user_id:
            msg = f"恭喜你涩到了你自己！" + MessageSegment.image(file=await user_img(user_id))
            await yinpa.finish(msg, at_sender=True)
        
        X = random.randint(1, 100)
        
        # 检查是否为 CP
        cp_id = record_CP.get(group_id, {}).get(user_id, 0)
        if at == cp_id:
            # 目标是自己的 CP
            if 0 < X <= yinpa_CP:
                yinpa_id = at
                tips = "恭喜你涩到了你的老婆！"
            else:
                await yinpa.finish("你的老婆拒绝和你涩涩！", at_sender=True)
        else:
            # 目标不是 CP
            if 0 < X <= yinpa_HE:
                yinpa_id = at
                tips = "恭喜你涩到了群友！"
            else:
                await yinpa.finish("涩涩警察出现！不许涩涩！", at_sender=True)
    
    else:
        # --- 随机目标逻辑 ---
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - last_sent_time_filter
        
        # 筛选卡池
        yinpa_ids = [
            member['user_id'] for member in member_list
            if (member['user_id'] not in protect_set)
            and (member['user_id'] != user_id)
            and (member["last_sent_time"] > lastmonth)
        ]
        
        if yinpa_ids:
            yinpa_id = random.choice(yinpa_ids)
        else:
            await yinpa.finish("卡池里没有可涩的人了！", at_sender=True)
    
    # --- 处理结果 ---
    if not yinpa_id:
        await yinpa.finish("不可以涩涩！", at_sender=True)
    
    # 更新记录
    record_yinpa1[user_id] = record_yinpa1.get(user_id, 0) + 1
    save_record_yinpa1()
    
    record_yinpa2[yinpa_id] = record_yinpa2.get(yinpa_id, 0) + 1
    save_record_yinpa2()
    
    # 获取目标信息并发送结果
    member = await bot.get_group_member_info(group_id=group_id, user_id=yinpa_id)
    msg = tips + MessageSegment.image(
        file=await user_img(yinpa_id)) + f"『{member['card'] or member['nickname']}』！"
    
    await yinpa.finish(msg, at_sender=True)


# ============================================================
# 涩涩记录查询
# ============================================================

yinpa_list = on_command(
    "涩涩记录",
    aliases={"色色记录"},
    priority=10,
    block=True,
    rule=create_exact_command_rule("涩涩记录", {"色色记录"}, extra_rule=check_plugin_enabled)
)


@yinpa_list.handle()
async def handle_yinpa_list(bot: Bot, event: GroupMessageEvent):
    """查看涩涩记录"""
    # 检查 yinpa 功能是否启用
    if not await check_yinpa_enabled(event):
        await yinpa_list.finish("本群禁止涩涩！")
    
    group_id = event.group_id
    msg_list = []
    
    # 获取群成员列表
    member_list = await bot.get_group_member_list(group_id=group_id)
    lastmonth = event.time - last_sent_time_filter
    protect_set = protect_list.get(group_id, set())
    
    # --- 输出卡池 ---
    member_list_filtered = [
        member for member in member_list 
        if member['user_id'] not in protect_set 
        and member["last_sent_time"] > lastmonth
    ]
    member_list_filtered.sort(key=lambda x: x["last_sent_time"], reverse=True)
    
    msg = "卡池：\n——————————————\n"
    msg += "\n".join([
        (member['card'] or member['nickname']) 
        for member in member_list_filtered[:80]
    ])
    
    msg_list.append({
        "type": "node",
        "data": {
            "name": "卡池",
            "uin": event.self_id,
            "content": MessageSegment.image(text_to_png(msg))
        }
    })
    
    # --- 输出透群友记录 ---
    record1 = [
        ((member['card'] or member['nickname']), times) 
        for member in member_list 
        if (times := record_yinpa1.get(member['user_id']))
    ]
    record1.sort(key=lambda x: x[1], reverse=True)
    
    if record1:
        msg = "\n".join([
            f"[align=left]{nickname}[/align][align=right]今日透群友 {times} 次[/align]" 
            for nickname, times in record1
        ])
        msg_list.append({
            "type": "node",
            "data": {
                "name": "记录①",
                "uin": event.self_id,
                "content": MessageSegment.image(
                    bbcode_to_png("涩涩记录①：\n——————————————\n" + msg)
                )
            }
        })
    
    # --- 输出被透记录 ---
    record2 = [
        ((member['card'] or member['nickname']), times) 
        for member in member_list 
        if (times := record_yinpa2.get(member['user_id']))
    ]
    record2.sort(key=lambda x: x[1], reverse=True)
    
    if record2:
        msg = "\n".join([
            f"[align=left]{nickname}[/align][align=right]今日被透 {times} 次[/align]" 
            for nickname, times in record2
        ])
        msg_list.append({
            "type": "node",
            "data": {
                "name": "记录②",
                "uin": event.self_id,
                "content": MessageSegment.image(
                    bbcode_to_png("涩涩记录②：\n——————————————\n" + msg)
                )
            }
        })
    
    # 发送结果
    if len(msg_list) > 1:
        # 有记录时发送合并转发
        await bot.send_group_forward_msg(group_id=group_id, messages=msg_list)
    else:
        # 只有卡池，没有记录
        await yinpa_list.finish("今天还没有人涩涩哦。")
