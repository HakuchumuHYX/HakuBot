# waifu/marry.py
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

# 从 .utils 导入辅助函数
from .utils import get_message_at, user_img, text_to_png

# 从 __init__.py 导入共享资源
from .__init__ import (
    # 规则
    check_plugin_enabled,
    is_plugin_enabled_internal,
    create_exact_command_rule,

    # 配置
    HE, BE, NTR,
    last_sent_time_filter,

    # CD管理
    check_cd, update_cd, PLUGIN_NAME,

    # 数据字典
    record_CP, record_waifu, record_lock, protect_list,

    # I/O
    save,
    record_CP_file, record_waifu_file, record_lock_file, protect_list_file
)

# --- 保护名单 ---

protect = on_command("娶群友保护",
                     priority=10,
                     block=True,
                     rule=create_exact_command_rule("娶群友保护", extra_rule=check_plugin_enabled)
                     )


@protect.handle()
async def _(bot: Bot, event: GroupMessageEvent, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER):
    group_id = event.group_id
    protect_set = protect_list.setdefault(group_id, set())
    at = get_message_at(event.message)
    if not at:
        protect_set.add(event.user_id)
        save(protect_list_file, protect_list)
        await protect.finish("保护成功！", at_sender=True)
    elif await permission(bot, event):
        protect_set.update(set(at))
        namelist = '\n'.join([(member['card'] or member['nickname']) for user_id in at if
                              (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))])
        save(protect_list_file, protect_list)
        await protect.finish(f"保护成功！\n保护名单为：\n{namelist}", at_sender=True)
    else:
        await protect.finish("保护失败。你无法为其他人设置保护。", at_sender=True)


unprotect = on_command("解除娶群友保护",
                       priority=10,
                       block=True,
                       rule=create_exact_command_rule("解除娶群友保护", extra_rule=check_plugin_enabled)
                       )


@unprotect.handle()
async def _(bot: Bot, event: GroupMessageEvent, permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER):
    group_id = event.group_id
    protect_set = protect_list.setdefault(group_id, set())
    at = get_message_at(event.message)
    if not at:
        user_id = event.user_id
        if user_id in protect_set:
            protect_set.discard(user_id)
            save(protect_list_file, protect_list)
            await unprotect.finish("解除保护成功！", at_sender=True)
        else:
            await unprotect.finish("你不在保护名单内。", at_sender=True)
    elif await permission(bot, event):
        valid_at = protect_set & set(at)
        if not valid_at:
            await unprotect.finish("保护名单内不存在指定成员。", at_sender=True)
        protect_set -= valid_at
        save(protect_list_file, protect_list)
        namelist = '\n'.join([(member['card'] or member['nickname']) for user_id in valid_at if
                              (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))])
        await unprotect.finish(f"解除保护成功！\n解除保护名单为：\n{namelist}", at_sender=True)
    else:
        await unprotect.finish("解除保护失败。你无法为其他人解除保护。", at_sender=True)


show_protect = on_command("查看保护名单",
                          priority=10,
                          block=True,
                          rule=create_exact_command_rule("查看保护名单", extra_rule=check_plugin_enabled)
                          )


@show_protect.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    protect_set = protect_list.get(group_id)
    if not protect_set:
        await show_protect.finish("保护名单为空")
    namelist = '\n'.join([(member['card'] or member['nickname']) for user_id in protect_set if
                          (member := await bot.get_group_member_info(group_id=group_id, user_id=user_id))])
    await show_protect.finish(MessageSegment.image(text_to_png(f"保护名单为：\n{namelist}")))


# --- 娶群友 ---

no_waifu = [
    "你没有娶到群友，强者注定孤独，加油！",
    "找不到对象.jpg",
    "恭喜你没有娶到老婆~",
    "さんが群友で結婚するであろうヒロインは、\n『自分の左手』です！"
]

happy_end = [
    "好耶~",
    "需要咱主持婚礼吗qwq",
    "不许秀恩爱！",
    "(响起婚礼进行曲♪)",
    "祝你们生八个。"
]


async def waifu_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """
    规则：娶群友
    """
    if not is_plugin_enabled_internal(str(event.group_id), str(event.user_id)):
        return False

    msg = event.message.extract_plain_text()
    if msg != "娶群友" and not (msg.startswith("娶群友") and get_message_at(event.message)):
        return False
    group_id = event.group_id
    user_id = event.user_id
    protect_set = protect_list.get(group_id, set())
    if user_id in protect_set:
        return False
    at = get_message_at(event.message)
    at = at[0] if at else None
    if at in protect_set:
        return False
    tips = "你的群友結婚对象是、"
    rec = record_CP.setdefault(group_id, {})

    waifu_id = 0

    if (waifu_id_check := rec.get(user_id)) and waifu_id_check != user_id:
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id_check)
        except:
            member = None
            waifu_id_check = user_id
        if member:
            if at and at != user_id:
                if waifu_id_check == at:
                    msg = "这是你的CP！" + random.choice(happy_end) + MessageSegment.image(
                        file=await user_img(waifu_id_check))
                    if user_id in record_waifu.get(group_id, set()):
                        lock = record_lock.setdefault(group_id, {})
                        lock[waifu_id_check] = user_id
                        lock[user_id] = waifu_id_check
                        save(record_lock_file, record_lock)
                        msg += "\ncp已锁！"
                else:
                    msg = "你已经有CP了，不许花心哦~" + MessageSegment.image(
                        file=await user_img(waifu_id_check)) + f"你的CP：{member['card'] or member['nickname']}"
            else:
                msg = tips + MessageSegment.image(
                    file=await user_img(waifu_id_check)) + f"『{member['card'] or member['nickname']}』！"
            await bot.send(event, msg, at_sender=True)
            return False

    if at:
        if at == rec.get(at):
            X = HE
            del rec[at]
        else:
            X = random.randint(1, 100)

        if 0 < X <= HE:
            waifu_id = at
            tips = "恭喜你娶到了群友!\n" + tips
        elif HE < X <= BE:
            waifu_id = user_id
        else:
            pass

    if not waifu_id:
        group_id = event.group_id
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - last_sent_time_filter
        rule_out = protect_set | set(rec.keys())
        waifu_ids = [user_id for member in member_list if
                     (user_id := member['user_id']) not in rule_out and member["last_sent_time"] > lastmonth]
        if waifu_ids:
            waifu_id = random.choice(list(waifu_ids))
        else:
            msg = "群友已经被娶光了、\n" + random.choice(no_waifu)
            await bot.send(event, msg, at_sender=True)
            return False
    state["waifu"] = waifu_id, tips
    return True


waifu = on_message(rule=waifu_rule, priority=10, block=True)


@waifu.handle()
async def _(bot: Bot, event: GroupMessageEvent, state: T_State):
    group_id = event.group_id
    user_id = event.user_id
    waifu_id, tips = state["waifu"]
    rec = record_CP.setdefault(group_id, {})
    if waifu_id == user_id:
        rec[user_id] = user_id
        save(record_CP_file, record_CP)
        await waifu.finish(random.choice(no_waifu), at_sender=True)
    waifu_set = record_waifu.setdefault(group_id, set())
    if waifu_id in rec:
        waifu_cp = rec[waifu_id]
        member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_cp)
        msg = "人家已经名花有主了~" + MessageSegment.image(file=await user_img(waifu_cp)) + "ta的cp：" + (
                member['card'] or member['nickname'])
        if waifu_id in record_lock.get(group_id, {}).keys():
            await waifu.finish(msg + "\n本对cp已锁！", at_sender=True)
        X = random.randint(1, 100)
        if X > NTR:
            rec[user_id] = user_id
            save(record_CP_file, record_CP)
            await waifu.finish(msg, at_sender=True)
        else:
            rec.pop(waifu_cp)
            waifu_set.discard(waifu_cp)
            await waifu.send(msg + "\n但是...", at_sender=True)
            await asyncio.sleep(1)

    rec[user_id] = waifu_id
    rec[waifu_id] = user_id
    waifu_set.add(waifu_id)
    member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id)
    msg = tips + MessageSegment.image(file=await user_img(waifu_id)) + f"『{(member['card'] or member['nickname'])}』！"
    save(record_waifu_file, record_waifu)
    save(record_CP_file, record_CP)
    await waifu.finish(msg, at_sender=True)


# --- 离婚 ---

async def bye_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """离婚命令规则"""
    if not await check_plugin_enabled(event):
        return False
    msg = event.message.extract_plain_text().strip()
    if msg not in ["离婚", "分手"]:
        return False
    return (isinstance(event, GroupMessageEvent) and
            event.group_id in record_CP and
            record_CP[event.group_id].get(event.user_id, event.user_id) != event.user_id)


bye = on_command("离婚",
                 aliases={"分手"},
                 rule=bye_rule,
                 priority=10,
                 block=True
                 )


@bye.handle()
async def _(event: GroupMessageEvent):
    group_id_int = event.group_id
    user_id_int = event.user_id
    group_id_str = str(event.group_id)
    user_id_str = str(event.user_id)

    cd_feature_id = f"{PLUGIN_NAME}:bye"
    remaining_cd = check_cd(cd_feature_id, group_id_str, user_id_str)

    if remaining_cd > 0:
        await bye.finish(f"你的离婚cd还有 {remaining_cd} 秒。", at_sender=True)
        return

    rec = record_CP[group_id_int]
    waifu_set = record_waifu.setdefault(group_id_int, set())
    waifu_id = rec[user_id_int]
    del rec[user_id_int]
    del rec[waifu_id]
    waifu_set.discard(user_id_int)
    waifu_set.discard(waifu_id)

    if group_id_int in record_lock:
        if waifu_id in record_lock[group_id_int]:
            del record_lock[group_id_int][waifu_id]
        if user_id_int in record_lock[group_id_int]:
            del record_lock[group_id_int][user_id_int]
        save(record_lock_file, record_lock)

    save(record_waifu_file, record_waifu)
    save(record_CP_file, record_CP)

    update_cd(cd_feature_id, group_id_str, user_id_str)

    if random.randint(1, 2) == 1:
        await bye.finish(random.choice(("嗯。", "...", "好。")))
    else:
        await bye.finish(random.choice(("嗯。", "...", "好。")))


# --- 列表查询 ---

waifu_list = on_command("查看群友卡池",
                        aliases={"群友卡池"},
                        priority=10,
                        block=True,
                        rule=create_exact_command_rule("查看群友卡池", {"群友卡池"}, extra_rule=check_plugin_enabled)
                        )


@waifu_list.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    member_list = await bot.get_group_member_list(group_id=group_id)
    lastmonth = event.time - last_sent_time_filter
    cp_records = record_CP.get(group_id, {})
    rule_out = protect_list.get(group_id, set()) | set(cp_records.keys())
    member_list = [member for member in member_list if
                   member['user_id'] not in rule_out and member["last_sent_time"] > lastmonth]
    member_list.sort(key=lambda x: x["last_sent_time"], reverse=True)
    if member_list:
        msg = "卡池：\n——————————————\n"
        for member in member_list[:80]:
            msg += f"{member['card'] or member['nickname']}\n"
        await waifu_list.finish(MessageSegment.image(text_to_png(msg[:-1])))
    else:
        await waifu_list.finish("群友已经被娶光了。")


cp_list = on_command("本群CP",
                     aliases={"本群cp"},
                     priority=10,
                     block=True,
                     rule=create_exact_command_rule("本群CP", {"本群cp"}, extra_rule=check_plugin_enabled)
                     )


@cp_list.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    waifu_set = record_waifu.get(group_id)
    if not waifu_set:
        await cp_list.finish("本群暂无cp哦~")
    rec = record_CP.get(group_id, {})
    msg = ""
    for waifu_id in waifu_set:
        user_id = rec[waifu_id]
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
            niknameA = member['card'] or member['nickname']
        except:
            niknameA = ""
        try:
            member = await bot.get_group_member_info(group_id=group_id, user_id=waifu_id)
            niknameB = member['card'] or member['nickname']
        except:
            niknameB = ""

        msg += f"♥ {niknameA} | {niknameB}\n"
    await cp_list.finish(MessageSegment.image(text_to_png("本群CP：\n——————————————\n" + msg[:-1])))