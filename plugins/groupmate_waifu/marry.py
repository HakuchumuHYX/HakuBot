"""
groupmate_waifu/marry.py
娶群友功能：娶群友、保护名单、离婚、列表查询等
"""

import random
import asyncio

from nonebot.plugin.on import on_command, on_message
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11 import (
    GROUP_ADMIN,
    GROUP_OWNER,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)

# 本地模块导入
from .constants import PLUGIN_NAME, NO_WAIFU_MESSAGES, HAPPY_END_MESSAGES, BYE_MESSAGES
from .utils import get_message_at, user_img, text_to_png
from .rules import check_plugin_enabled, is_plugin_enabled, is_bye_enabled
from .data_manager import (
    # 配置
    HE, BE, NTR, last_sent_time_filter,
    # 数据字典
    record_CP, record_waifu, record_lock, protect_list,
    # 保存函数
    save_record_CP, save_record_waifu, save_record_lock, save_protect_list,
)

# 外部模块导入
from ..utils.common import create_exact_command_rule
from ..plugin_manager.cd_manager import check_cd, update_cd


# ============================================================
# 保护名单相关命令
# ============================================================

protect = on_command(
    "娶群友保护",
    priority=10,
    block=True,
    rule=create_exact_command_rule("娶群友保护", extra_rule=check_plugin_enabled)
)


@protect.handle()
async def handle_protect(bot: Bot, event: GroupMessageEvent):
    """添加保护名单"""
    permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
    group_id = event.group_id
    protect_set = protect_list.setdefault(group_id, set())
    at = get_message_at(event.message)
    
    if not at:
        # 保护自己
        protect_set.add(event.user_id)
        save_protect_list()
        await protect.finish("保护成功！", at_sender=True)
    elif await permission(bot, event):
        # 管理员保护他人
        protect_set.update(set(at))
        namelist = '\n'.join([
            (member['card'] or member['nickname']) 
            for user_id in at 
            if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
        ])
        save_protect_list()
        await protect.finish(f"保护成功！\n保护名单为：\n{namelist}", at_sender=True)
    else:
        await protect.finish("保护失败。你无法为其他人设置保护。", at_sender=True)


unprotect = on_command(
    "解除娶群友保护",
    priority=10,
    block=True,
    rule=create_exact_command_rule("解除娶群友保护", extra_rule=check_plugin_enabled)
)


@unprotect.handle()
async def handle_unprotect(bot: Bot, event: GroupMessageEvent):
    """解除保护名单"""
    permission = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
    group_id = event.group_id
    protect_set = protect_list.setdefault(group_id, set())
    at = get_message_at(event.message)
    
    if not at:
        # 解除自己的保护
        user_id = event.user_id
        if user_id in protect_set:
            protect_set.discard(user_id)
            save_protect_list()
            await unprotect.finish("解除保护成功！", at_sender=True)
        else:
            await unprotect.finish("你不在保护名单内。", at_sender=True)
    elif await permission(bot, event):
        # 管理员解除他人保护
        valid_at = protect_set & set(at)
        if not valid_at:
            await unprotect.finish("保护名单内不存在指定成员。", at_sender=True)
        protect_set -= valid_at
        save_protect_list()
        namelist = '\n'.join([
            (member['card'] or member['nickname']) 
            for user_id in valid_at 
            if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
        ])
        await unprotect.finish(f"解除保护成功！\n解除保护名单为：\n{namelist}", at_sender=True)
    else:
        await unprotect.finish("解除保护失败。你无法为其他人解除保护。", at_sender=True)


show_protect = on_command(
    "查看保护名单",
    priority=10,
    block=True,
    rule=create_exact_command_rule("查看保护名单", extra_rule=check_plugin_enabled)
)


@show_protect.handle()
async def handle_show_protect(bot: Bot, event: GroupMessageEvent):
    """查看保护名单"""
    group_id = event.group_id
    protect_set = protect_list.get(group_id)
    
    if not protect_set:
        await show_protect.finish("保护名单为空")
    
    namelist = '\n'.join([
        (member['card'] or member['nickname']) 
        for user_id in protect_set 
        if (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))
    ])
    await show_protect.finish(MessageSegment.image(text_to_png(f"保护名单为：\n{namelist}")))


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
    
    # 检查发送者是否被保护
    group_id = event.group_id
    user_id = event.user_id
    protect_set = protect_list.get(group_id, set())
    
    if user_id in protect_set:
        return False
    
    # 检查 @ 的目标是否被保护
    at = at_list[0] if at_list else None
    if at and at in protect_set:
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
    
    tips = "你的群友結婚对象是、"
    rec = record_CP.setdefault(group_id, {})
    waifu_set = record_waifu.setdefault(group_id, set())
    
    # --- 1. 检查用户是否已有 CP ---
    existing_waifu_id = rec.get(user_id)
    if existing_waifu_id and existing_waifu_id != user_id:
        # 用户已有 CP
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=existing_waifu_id)
        except Exception:
            member = None
            # CP 已不在群内，清除记录
            del rec[user_id]
            if existing_waifu_id in rec:
                del rec[existing_waifu_id]
            waifu_set.discard(user_id)
            waifu_set.discard(existing_waifu_id)
            save_record_CP()
            save_record_waifu()
        
        if member:
            if at and at != user_id:
                if existing_waifu_id == at:
                    # @ 的是自己的 CP
                    msg = "这是你的CP！" + random.choice(HAPPY_END_MESSAGES) + MessageSegment.image(
                        file=await user_img(existing_waifu_id))
                    # 如果自己是被娶的，可以锁定
                    if user_id in waifu_set:
                        lock = record_lock.setdefault(group_id, {})
                        lock[existing_waifu_id] = user_id
                        lock[user_id] = existing_waifu_id
                        save_record_lock()
                        msg += "\ncp已锁！"
                else:
                    # @ 的不是自己的 CP
                    msg = "你已经有CP了，不许花心哦~" + MessageSegment.image(
                        file=await user_img(existing_waifu_id)) + f"你的CP：{member['card'] or member['nickname']}"
            else:
                # 没有 @ 人，显示当前 CP
                msg = tips + MessageSegment.image(
                    file=await user_img(existing_waifu_id)) + f"『{member['card'] or member['nickname']}』！"
            
            await waifu.finish(msg, at_sender=True)
    
    # --- 2. 用户没有 CP，尝试娶群友 ---
    waifu_id = None
    
    if at:
        # 指定目标
        if at == user_id:
            await waifu.finish("不可以娶自己哦！", at_sender=True)
        
        # 检查目标是否是单身状态
        if at == rec.get(at):
            # 目标之前是单身，增加成功率
            X = HE
            del rec[at]
        else:
            X = random.randint(1, 100)
        
        if 0 < X <= HE:
            waifu_id = at
            tips = "恭喜你娶到了群友!\n" + tips
        elif HE < X <= BE:
            # BE：自己变成单身
            waifu_id = user_id
        else:
            # 失败
            try:
                member = await bot.get_group_member_info(group_id=group_id, user_id=at)
                name = member['card'] or member['nickname']
            except Exception:
                name = "TA"
            await waifu.finish(f"你没能娶到 {name}！", at_sender=True)
    
    if not waifu_id:
        # 随机抽取
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - last_sent_time_filter
        protect_set = protect_list.get(group_id, set())
        rule_out = protect_set | set(rec.keys())
        
        waifu_ids = [
            member['user_id'] for member in member_list 
            if member['user_id'] not in rule_out 
            and member["last_sent_time"] > lastmonth
        ]
        
        if waifu_ids:
            waifu_id = random.choice(waifu_ids)
        else:
            msg = "群友已经被娶光了、\n" + random.choice(NO_WAIFU_MESSAGES)
            await waifu.finish(msg, at_sender=True)
    
    # --- 3. 处理娶群友结果 ---
    if waifu_id == user_id:
        # 单身
        rec[user_id] = user_id
        save_record_CP()
        await waifu.finish(random.choice(NO_WAIFU_MESSAGES), at_sender=True)
    
    # 检查目标是否已有 CP
    if waifu_id in rec:
        waifu_cp = rec[waifu_id]
        member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_cp)
        msg = "人家已经名花有主了~" + MessageSegment.image(
            file=await user_img(waifu_cp)) + "ta的cp：" + (member['card'] or member['nickname'])
        
        # 检查是否被锁定
        if waifu_id in record_lock.get(group_id, {}):
            rec[user_id] = user_id
            save_record_CP()
            await waifu.finish(msg + "\n本对cp已锁！", at_sender=True)
        
        # 尝试 NTR
        X = random.randint(1, 100)
        if X > NTR:
            rec[user_id] = user_id
            save_record_CP()
            await waifu.finish(msg, at_sender=True)
        else:
            # NTR 成功
            rec.pop(waifu_cp, None)
            waifu_set.discard(waifu_cp)
            await waifu.send(msg + "\n但是...", at_sender=True)
            await asyncio.sleep(1)
    
    # 成功配对
    rec[user_id] = waifu_id
    rec[waifu_id] = user_id
    waifu_set.add(waifu_id)
    
    member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id)
    msg = tips + MessageSegment.image(
        file=await user_img(waifu_id)) + f"『{member['card'] or member['nickname']}』！"
    
    save_record_waifu()
    save_record_CP()
    
    await waifu.finish(msg, at_sender=True)


# ============================================================
# 离婚功能
# ============================================================

async def bye_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """离婚命令的匹配规则"""
    # 检查插件启用
    if not is_plugin_enabled(str(event.group_id), str(event.user_id)):
        return False
    
    # 检查 bye 功能启用
    if not is_bye_enabled(str(event.group_id), str(event.user_id)):
        return False
    
    # 检查消息格式
    msg = event.message.extract_plain_text().strip()
    if msg not in ["离婚", "分手"]:
        return False
    
    # 检查用户是否有 CP
    group_id = event.group_id
    user_id = event.user_id
    
    if group_id not in record_CP:
        return False
    
    waifu_id = record_CP[group_id].get(user_id)
    if not waifu_id or waifu_id == user_id:
        return False
    
    return True


bye = on_command(
    "离婚",
    aliases={"分手"},
    rule=bye_rule,
    priority=10,
    block=True
)


@bye.handle()
async def handle_bye(event: GroupMessageEvent):
    """离婚处理逻辑"""
    group_id = event.group_id
    user_id = event.user_id
    group_id_str = str(group_id)
    user_id_str = str(user_id)
    
    # CD 检查
    cd_feature_id = f"{PLUGIN_NAME}:bye"
    remaining_cd = check_cd(cd_feature_id, group_id_str, user_id_str)
    
    if remaining_cd > 0:
        await bye.finish(f"你的离婚cd还有 {remaining_cd} 秒。", at_sender=True)
    
    # 执行离婚
    rec = record_CP[group_id]
    waifu_set = record_waifu.setdefault(group_id, set())
    waifu_id = rec[user_id]
    
    del rec[user_id]
    del rec[waifu_id]
    waifu_set.discard(user_id)
    waifu_set.discard(waifu_id)
    
    # 解除锁定
    if group_id in record_lock:
        record_lock[group_id].pop(waifu_id, None)
        record_lock[group_id].pop(user_id, None)
        save_record_lock()
    
    save_record_waifu()
    save_record_CP()
    
    # 更新 CD
    update_cd(cd_feature_id, group_id_str, user_id_str)
    
    await bye.finish(random.choice(BYE_MESSAGES))


# ============================================================
# 列表查询功能
# ============================================================

waifu_list = on_command(
    "查看群友卡池",
    aliases={"群友卡池"},
    priority=10,
    block=True,
    rule=create_exact_command_rule("查看群友卡池", {"群友卡池"}, extra_rule=check_plugin_enabled)
)


@waifu_list.handle()
async def handle_waifu_list(bot: Bot, event: GroupMessageEvent):
    """查看群友卡池"""
    group_id = event.group_id
    member_list = await bot.get_group_member_list(group_id=group_id)
    lastmonth = event.time - last_sent_time_filter
    
    cp_records = record_CP.get(group_id, {})
    protect_set = protect_list.get(group_id, set())
    rule_out = protect_set | set(cp_records.keys())
    
    member_list = [
        member for member in member_list 
        if member['user_id'] not in rule_out 
        and member["last_sent_time"] > lastmonth
    ]
    member_list.sort(key=lambda x: x["last_sent_time"], reverse=True)
    
    if member_list:
        msg = "卡池：\n——————————————\n"
        for member in member_list[:80]:
            msg += f"{member['card'] or member['nickname']}\n"
        await waifu_list.finish(MessageSegment.image(text_to_png(msg[:-1])))
    else:
        await waifu_list.finish("群友已经被娶光了。")


cp_list = on_command(
    "本群CP",
    aliases={"本群cp"},
    priority=10,
    block=True,
    rule=create_exact_command_rule("本群CP", {"本群cp"}, extra_rule=check_plugin_enabled)
)


@cp_list.handle()
async def handle_cp_list(bot: Bot, event: GroupMessageEvent):
    """查看本群 CP 列表"""
    group_id = event.group_id
    waifu_set = record_waifu.get(group_id)
    
    if not waifu_set:
        await cp_list.finish("本群暂无cp哦~")
    
    rec = record_CP.get(group_id, {})
    msg = ""
    
    for waifu_id in waifu_set:
        user_id = rec.get(waifu_id)
        if not user_id:
            continue
        
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
            niknameA = member['card'] or member['nickname']
        except Exception:
            niknameA = str(user_id)
        
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id)
            niknameB = member['card'] or member['nickname']
        except Exception:
            niknameB = str(waifu_id)
        
        msg += f"♥ {niknameA} | {niknameB}\n"
    
    if msg:
        await cp_list.finish(MessageSegment.image(text_to_png("本群CP：\n——————————————\n" + msg[:-1])))
    else:
        await cp_list.finish("本群暂无cp哦~")
