"""娶群友核心 matcher.

This handler keeps Bot API calls and message sending here; state mutation and
pure marriage decisions live in `service.py`.
"""

import random
import asyncio

from nonebot.plugin.on import on_message
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

# 本地模块导入
from .. import service
from ..constants import NO_WAIFU_MESSAGES, HAPPY_END_MESSAGES
from ..render import build_avatar_message, finish_with_fallback, send_with_fallback
from ..utils import get_message_at
from ..rules import is_plugin_enabled

# ============================================================
# 娶群友核心功能
# ============================================================

async def waifu_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """
    娶群友命令的匹配规则
    只做基本的格式匹配和权限检查，不处理业务逻辑
    """
    # 检查插件是否启用
    if not is_plugin_enabled(str(event.group_id), str(event.user_id)):
        return False
    
    # 检查消息格式
    msg = event.message.extract_plain_text()
    at_list = get_message_at(event.message)
    
    if msg != "娶群友" and not (msg.startswith("娶群友") and at_list):
        return False
    
    group_id = event.group_id
    user_id = event.user_id

    if service.is_protected(group_id, user_id):
        return False

    at = at_list[0] if at_list else None
    if at and service.is_protected(group_id, at):
        return False
    
    # 将解析结果存入 state
    state["at_target"] = at
    return True


waifu = on_message(rule=waifu_rule, priority=10, block=True)


@waifu.handle()
async def handle_waifu(bot: Bot, event: GroupMessageEvent, state: T_State):
    """娶群友核心处理逻辑"""
    group_id = event.group_id
    user_id = event.user_id
    at = state.get("at_target")
    
    happy_threshold, bad_threshold, ntr_threshold = service.get_marriage_thresholds()
    tips = "你的群友結婚对象是、"
    rec = service.ensure_group_cp_records(group_id)
    
    # --- 1. 检查用户是否已有 CP ---
    existing_waifu_id = rec.get(user_id)
    if existing_waifu_id and existing_waifu_id != user_id:
        # 用户已有 CP
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=existing_waifu_id)
        except Exception:
            member = None
            # CP 已不在群内，清除记录
            service.remove_couple(group_id, user_id)
        
        if member:
            if at and at != user_id:
                if existing_waifu_id == at:
                    # @ 的是自己的 CP
                    msg, fallback = await build_avatar_message(
                        "这是你的CP！" + random.choice(HAPPY_END_MESSAGES),
                        existing_waifu_id,
                    )
                    # 如果自己是被娶的，可以锁定
                    if service.is_waifu_side(group_id, user_id):
                        service.lock_couple(group_id, existing_waifu_id, user_id)
                        msg += "\ncp已锁！"
                        fallback += "\ncp已锁！"
                else:
                    # @ 的不是自己的 CP
                    msg, fallback = await build_avatar_message(
                        "你已经有CP了，不许花心哦~",
                        existing_waifu_id,
                        f"你的CP：{member['card'] or member['nickname']}",
                    )
            else:
                # 没有 @ 人，显示当前 CP
                msg, fallback = await build_avatar_message(
                    tips,
                    existing_waifu_id,
                    f"『{member['card'] or member['nickname']}』！",
                )

            await finish_with_fallback(waifu, msg, fallback, at_sender=True)
    
    # --- 2. 用户没有 CP，尝试娶群友 ---
    waifu_id = None
    
    if at:
        waifu_id, tips, error_message = service.resolve_marriage_target(
            group_id,
            user_id,
            at,
            random.randint(1, 100),
            happy_threshold,
            bad_threshold,
        )
        if error_message == "TARGET_FAILED":
            try:
                member = await bot.get_group_member_info(group_id=group_id, user_id=at)
                name = member['card'] or member['nickname']
            except Exception:
                name = "TA"
            await waifu.finish(f"你没能娶到 {name}！", at_sender=True)
        if error_message:
            await waifu.finish(error_message, at_sender=True)
    
    if not waifu_id:
        # 随机抽取
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - service.get_last_sent_time_filter()
        waifu_ids = [
            member["user_id"]
            for member in service.get_marriage_pool_members(group_id, member_list, lastmonth)
        ]
        
        if waifu_ids:
            waifu_id = random.choice(waifu_ids)
        else:
            msg = "群友已经被娶光了、\n" + random.choice(NO_WAIFU_MESSAGES)
            await waifu.finish(msg, at_sender=True)
    
    # --- 3. 处理娶群友结果 ---
    if waifu_id == user_id:
        # 单身
        service.set_single(group_id, user_id)
        await waifu.finish(random.choice(NO_WAIFU_MESSAGES), at_sender=True)
    
    # 检查目标是否已有 CP
    waifu_cp = service.get_partner(group_id, waifu_id)
    if waifu_cp:
        member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_cp)
        msg, fallback = await build_avatar_message(
            "人家已经名花有主了~",
            waifu_cp,
            "ta的cp：" + (member['card'] or member['nickname']),
        )

        ntr_result = service.resolve_taken_marriage_target(
            group_id,
            user_id,
            waifu_id,
            random.randint(1, 100),
            ntr_threshold,
        )
        if ntr_result == "locked":
            await finish_with_fallback(waifu, msg + "\n本对cp已锁！", fallback + "\n本对cp已锁！", at_sender=True)
        if ntr_result == "failed":
            await finish_with_fallback(waifu, msg, fallback, at_sender=True)
        if ntr_result == "ntr":
            await send_with_fallback(waifu, msg + "\n但是...", fallback + "\n但是...", at_sender=True)
            await asyncio.sleep(1)
    
    service.set_couple(group_id, user_id, waifu_id)

    member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id)
    msg, fallback = await build_avatar_message(
        tips,
        waifu_id,
        f"『{member['card'] or member['nickname']}』！",
    )

    await finish_with_fallback(waifu, msg, fallback, at_sender=True)
