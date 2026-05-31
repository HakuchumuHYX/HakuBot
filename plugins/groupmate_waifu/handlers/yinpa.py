"""透群友 and 涩涩记录 matchers.

Target resolution and record aggregation live in `service.py`; this module keeps
matcher wiring, Bot API calls, and message sending.
"""

import random

from nonebot.plugin.on import on_command, on_message
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
)

# 本地模块导入
from .. import service
from ..render import (
    build_avatar_message,
    finish_with_fallback,
    make_forward_node,
    render_member_pool,
    render_yinpa_record,
)
from ..utils import get_message_at
from ..rules import check_plugin_enabled, check_yinpa_enabled, is_yinpa_enabled

# 外部模块导入
from ...utils.common import create_exact_command_rule


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
    if service.is_protected(group_id, user_id):
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
    
    normal_threshold, cp_threshold = service.get_yinpa_thresholds()
    tips = "你的涩涩对象是、"
    yinpa_id = None
    
    if at:
        if at == user_id:
            msg, fallback = await build_avatar_message("恭喜你涩到了你自己！", user_id)
            await finish_with_fallback(yinpa, msg, fallback, at_sender=True)

        yinpa_id, tips, error_message = service.resolve_yinpa_target(
            group_id,
            user_id,
            at,
            random.randint(1, 100),
            cp_threshold,
            normal_threshold,
        )
        if error_message:
            await yinpa.finish(error_message, at_sender=True)

    else:
        # --- 随机目标逻辑 ---
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - service.get_last_sent_time_filter()
        
        yinpa_ids = [
            member["user_id"]
            for member in service.get_yinpa_pool_members(
                group_id,
                member_list,
                lastmonth,
                excluded_user_id=user_id,
            )
        ]
        
        if yinpa_ids:
            yinpa_id = random.choice(yinpa_ids)
        else:
            await yinpa.finish("卡池里没有可涩的人了！", at_sender=True)
    
    # --- 处理结果 ---
    if not yinpa_id:
        await yinpa.finish("不可以涩涩！", at_sender=True)
    
    service.record_yinpa(user_id, yinpa_id)

    # 获取目标信息并发送结果
    member = await bot.get_group_member_info(group_id=group_id, user_id=yinpa_id)
    msg, fallback = await build_avatar_message(
        tips,
        yinpa_id,
        f"『{member['card'] or member['nickname']}』！",
    )

    await finish_with_fallback(yinpa, msg, fallback, at_sender=True)


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
    lastmonth = event.time - service.get_last_sent_time_filter()

    # --- 输出卡池 ---
    member_list_filtered = service.get_yinpa_pool_members(group_id, member_list, lastmonth)
    member_list_filtered.sort(key=lambda x: x["last_sent_time"], reverse=True)
    
    msg_list.append(make_forward_node(
        "卡池",
        event.self_id,
        render_member_pool("卡池", member_list_filtered),
    ))
    
    # --- 输出透群友记录 ---
    record1 = service.get_yinpa_actor_record_rows(member_list)
    
    if record1:
        msg_list.append(make_forward_node(
            "记录①",
            event.self_id,
            render_yinpa_record("涩涩记录①", record1, "今日透群友"),
        ))
    
    # --- 输出被透记录 ---
    record2 = service.get_yinpa_target_record_rows(member_list)
    
    if record2:
        msg_list.append(make_forward_node(
            "记录②",
            event.self_id,
            render_yinpa_record("涩涩记录②", record2, "今日被透"),
        ))
    
    # 发送结果
    if len(msg_list) > 1:
        # 有记录时发送合并转发
        await bot.send_group_forward_msg(group_id=group_id, messages=msg_list)
    else:
        # 只有卡池，没有记录
        await yinpa_list.finish("今天还没有人涩涩哦。")
