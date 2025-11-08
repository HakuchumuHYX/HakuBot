# waifu/__init__.py
from nonebot import require
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
from nonebot.log import logger
import nonebot
import os
import random
import asyncio
import time

from pathlib import Path

from .utils import *
from .config import Config

from nonebot.plugin import PluginMetadata
# 在已有的导入后面添加
from nonebot import get_loaded_plugins
from nonebot.plugin import Plugin
from ..utils.common import create_exact_command_rule

# vvvvvv 【修改点：统一导入插件管理API】 vvvvvv
from nonebot.rule import Rule  # 导入 Rule

try:
    # 导入功能开关API (带 user_id 检查)
    from ..plugin_manager.enable import is_plugin_enabled as check_plugin
    from ..plugin_manager.enable import is_feature_enabled

    # 导入CD管理API (带 user_id 检查)
    from ..plugin_manager.cd_manager import check_cd, update_cd

    PLUGIN_MANAGER_LOADED = True

except ImportError:
    # 如果 plugin_manager 未加载，定义回退函数（始终返回 True/0）
    PLUGIN_MANAGER_LOADED = False


    # 定义回退函数（签名必须与导入的函数一致）
    def check_plugin(plugin_name: str, group_id: str, user_id: str) -> bool:
        return True  # 默认启用


    def is_feature_enabled(plugin_name: str, feature_name: str, group_id: str, user_id: str) -> bool:
        return True  # 默认启用


    def check_cd(plugin_id: str, group_id: str, user_id: str) -> int:
        return 0  # 默认无CD


    def update_cd(plugin_id: str, group_id: str, user_id: str):
        pass  # 默认无操作
# ^^^^^^ 【修改点结束】 ^^^^^^


# 添加插件标识符（用于插件管理器）
PLUGIN_NAME = "groupmate_waifu"

__plugin_meta__ = PluginMetadata(
    name="娶群友",
    description="娶群友",
    usage="娶群友",
    config=Config,
    extra={}
)

# 加载全局配置
global_config = nonebot.get_driver().config
waifu_config = Config.parse_obj(global_config.dict())
# waifu_cd_bye = waifu_config.waifu_cd_bye  <- 【修改点：移除】
waifu_save = waifu_config.waifu_save
waifu_reset = waifu_config.waifu_reset
last_sent_time_filter = waifu_config.waifu_last_sent_time_filter
HE = waifu_config.waifu_he
BE = HE + waifu_config.waifu_be
NTR = waifu_config.waifu_ntr
yinpa_HE = waifu_config.yinpa_he
yinpa_BE = yinpa_HE + waifu_config.yinpa_be
yinpa_CP = waifu_config.yinpa_cp
yinpa_CP = yinpa_HE if yinpa_CP == 0 else yinpa_CP

# 判断文件时效
timestr = time.strftime('%Y-%m-%d', time.localtime(time.time()))
timeArray = time.strptime(timestr, '%Y-%m-%d')
Zero_today = time.mktime(timeArray)


# vvvvvv 【修改点：重构插件管理检查函数，以传递 user_id】 vvvvvv

# 插件管理器集成
def is_plugin_enabled_internal(group_id: str, user_id: str) -> bool:  # 【修改：添加user_id】
    """(内部)检查插件是否在指定群启用"""
    try:
        # 尝试从插件管理器导入检查函数 (【修改：传入user_id】)
        return check_plugin(PLUGIN_NAME, group_id, user_id)
    except (ImportError, TypeError):  # 【修改：捕获TypeError以防check_plugin签名不匹配】
        # 如果插件管理器未安装，默认启用
        return True


async def check_plugin_enabled(event: GroupMessageEvent) -> bool:
    """(Rule用)检查插件是否在当前群启用"""
    # 【修改：传入user_id】
    return is_plugin_enabled_internal(str(event.group_id), str(event.user_id))


# 添加子功能检查函数
def is_yinpa_enabled_internal(group_id: str, user_id: str) -> bool:  # 【修改：添加user_id】
    """(内部)检查透群友功能是否在指定群启用"""
    try:
        # 【修改：传入user_id】
        return is_feature_enabled(PLUGIN_NAME, "yinpa", group_id, user_id)
    except (ImportError, TypeError):  # 【修改：捕获TypeError】
        # 如果插件管理器未安装，默认启用
        return True


async def check_yinpa_enabled(event: GroupMessageEvent) -> bool:
    """(Rule用)检查透群友功能是否在当前群启用"""
    # 【修改：传入user_id】
    return is_yinpa_enabled_internal(str(event.group_id), str(event.user_id))


# ^^^^^^ 【修改点结束】 ^^^^^^


def load(file, waifu_reset):
    if waifu_reset and file.exists() and os.path.getmtime(file) > Zero_today:
        with open(file, 'r') as f:
            line = f.read()
            record = eval(line)
        logger.info(f"{file} 已加载")
    else:
        record = {}
        logger.info(f"{file} 已重置")
    return record


if waifu_save:
    def save(file, data):
        with open(file, "w", encoding="utf8") as f:
            f.write(str(data))
else:
    def save(file, data):
        pass

waifu_file = Path() / "data" / "waifu"

if not waifu_file.exists():
    os.makedirs(waifu_file)

record_CP_file = waifu_file / "record_CP"
record_waifu_file = waifu_file / "record_waifu"
record_lock_file = waifu_file / "record_lock"
record_yinpa1_file = waifu_file / "record_yinpa1"
record_yinpa2_file = waifu_file / "record_yinpa2"

record_CP = load(record_CP_file, waifu_reset)
record_waifu = load(record_waifu_file, waifu_reset)
record_lock = load(record_lock_file, waifu_reset)
record_yinpa1 = load(record_yinpa1_file, waifu_reset)
record_yinpa2 = load(record_yinpa2_file, waifu_reset)

protect_list_file = waifu_file / "list_protect"

if protect_list_file.exists():
    with open(protect_list_file, 'r') as f:
        line = f.read()
        protect_list = eval(line)
else:
    protect_list = {}
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

if waifu_reset:
    # 重置记录
    def reset_record():
        global record_CP, record_waifu, record_lock, record_yinpa1, record_yinpa2
        record_CP = {}
        record_waifu = {}
        record_lock = {}
        record_yinpa1 = {}
        record_yinpa2 = {}
        logger.info(f"娶群友记录已重置")
else:
    # 重置记录
    def reset_record():
        global record_CP, record_yinpa1, record_yinpa2
        for group_id in record_CP:
            for user_id in record_CP[group_id]:
                if record_CP[group_id][user_id] == user_id:
                    record_CP[group_id][user_id] = 0
        record_yinpa1 = {}
        record_yinpa2 = {}
        logger.info(f"娶群友记录已重置")

on_command("重置记录", priority=10, block=True).append_handler(reset_record)
scheduler.add_job(reset_record, "cron", hour=0, misfire_grace_time=120)

# 设置保护名单
# (保持不变, create_exact_command_rule 会使用新的 check_plugin_enabled)
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


# 移出保护名单
# (保持不变)
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


# 查看保护名单
# (保持不变)
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


# 娶群友

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
    # vvvvvv 【修改点：传入 user_id】 vvvvvv
    if not is_plugin_enabled_internal(str(event.group_id), str(event.user_id)):
        # ^^^^^^ 【修改点】 ^^^^^^
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

    waifu_id = 0  # 【修改点：确保 waifu_id 在if/else外被定义】

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
            pass  # waifu_id 保持 0, 进入后续随机逻辑

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


# 分手
# vvvvvv 【修改点：重构离婚命令以对接 plugin_manager CD】 vvvvvv

async def bye_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """离婚命令规则（已修改）"""
    if not await check_plugin_enabled(event):  # 检查插件是否启用
        return False

    msg = event.message.extract_plain_text().strip()
    if msg not in ["离婚", "分手"]:
        return False

    # 检查是否真的有CP
    return (isinstance(event, GroupMessageEvent) and
            event.group_id in record_CP and
            record_CP[event.group_id].get(event.user_id, event.user_id) != event.user_id)


bye = on_command("离婚",
                 aliases={"分手"},
                 rule=bye_rule,  # 使用修改后的规则
                 priority=10,
                 block=True
                 )


@bye.handle()
async def _(event: GroupMessageEvent):
    # 使用 int (event.group_id) 操作本地数据字典
    group_id_int = event.group_id
    user_id_int = event.user_id
    # 使用 str 操作 plugin_manager
    group_id_str = str(event.group_id)
    user_id_str = str(event.user_id)

    # 【修改点：使用 plugin_manager 检查CD】
    # 定义CD功能的唯一ID (请确保在 plugin_manager 的 readme 中注册此功能点)
    cd_feature_id = f"{PLUGIN_NAME}:bye"

    remaining_cd = check_cd(cd_feature_id, group_id_str, user_id_str)

    if remaining_cd > 0:
        await bye.finish(f"你的离婚cd还有 {remaining_cd} 秒。", at_sender=True)
        return

    # --- 执行离婚逻辑 (同原来, 使用 int) ---
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

    # 【修改点：更新CD (使用 str)】
    update_cd(cd_feature_id, group_id_str, user_id_str)

    if random.randint(1, 2) == 1:
        await bye.finish(random.choice(("嗯。", "...", "好。")))
    else:
        await bye.finish(random.choice(("嗯。", "...", "好。")))


# ^^^^^^ 【修改点结束】 ^^^^^^


# 查看娶群友卡池
# (保持不变)
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


# 查看本群CP
# (保持不变)
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


# 透群友
async def yinpa_rule(bot: Bot, event: GroupMessageEvent, state: T_State) -> bool:
    """
    规则：透群友
    """
    # vvvvvv 【修改点：传入 user_id】 vvvvvv
    if not is_plugin_enabled_internal(str(event.group_id), str(event.user_id)):
        # ^^^^^^ 【修改点】 ^^^^^^
        return False

    msg = event.message.extract_plain_text()
    if msg != "透群友" and not (msg.startswith("透群友") and get_message_at(event.message)):
        return False
    group_id = event.group_id
    user_id = event.user_id
    protect_set = protect_list.get(group_id, set())
    if user_id in protect_set:
        return False
    at = get_message_at(event.message)
    yinpa_id = None
    tips = "你的涩涩对象是、"
    if at:
        at = at[0]
        if at in protect_set:
            return False
        if at == user_id:
            msg = f"恭喜你涩到了你自己！" + MessageSegment.image(file=await user_img(user_id))
            await bot.send(event, msg, at_sender=True)
            return False
        X = random.randint(1, 100)
        if at == record_CP.get(group_id, {}).get(user_id, 0):
            if 0 < X <= yinpa_CP:
                yinpa_id = at
                tips = "恭喜你涩到了你的老婆！"
            else:
                await bot.send(event, "你的老婆拒绝和你涩涩！", at_sender=True)
                return False
        elif 0 < X <= yinpa_HE:
            yinpa_id = at
            tips = "恭喜你涩到了群友！"
        elif yinpa_HE < X <= yinpa_BE:
            yinpa_id = user_id
    if not yinpa_id:
        member_list = await bot.get_group_member_list(group_id=group_id)
        lastmonth = event.time - last_sent_time_filter
        yinpa_ids = [user_id for member in member_list if
                     (user_id := member['user_id']) not in protect_set and member["last_sent_time"] > lastmonth]
        if yinpa_ids:
            yinpa_id = random.choice(yinpa_ids)
        else:
            return False
    state["yinpa"] = yinpa_id, tips
    return True


yinpa = on_message(rule=yinpa_rule, priority=10, block=True)


@yinpa.handle()
async def _(bot: Bot, event: GroupMessageEvent, state: T_State):
    # (保持不变, check_yinpa_enabled 已被正确更新)
    if not await check_yinpa_enabled(event):
        await yinpa.finish("本群禁止涩涩！")
        return

    group_id = event.group_id
    user_id = event.user_id
    yinpa_id, tips = state["yinpa"]
    if yinpa_id == user_id:
        await yinpa.finish("不可以涩涩！", at_sender=True)
    else:
        record_yinpa1[user_id] = record_yinpa1.get(user_id, 0) + 1
        save(record_yinpa1_file, record_yinpa1)
        record_yinpa2[user_id] = record_yinpa2.get(yinpa_id, 0) + 1
        save(record_yinpa2_file, record_yinpa2)
        member = await bot.get_group_member_info(group_id=group_id, user_id=yinpa_id)
        msg = tips + MessageSegment.image(
            file=await user_img(yinpa_id)) + f"『{(member['card'] or member['nickname'])}』！"
        await yinpa.finish(msg, at_sender=True)


# 查看涩涩记录
# (保持不变)
yinpa_list = on_command("涩涩记录",
                        aliases={"色色记录"},
                        priority=10,
                        block=True,
                        rule=create_exact_command_rule("涩涩记录", {"色色记录"}, extra_rule=check_plugin_enabled)
                        )


@yinpa_list.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    # (保持不变, check_yinpa_enabled 已被正确更新)
    if not await check_yinpa_enabled(event):
        await yinpa_list.finish("本群禁止涩涩！")
        return
    group_id = event.group_id
    msg_list = []
    # 输出卡池
    member_list = await bot.get_group_member_list(group_id=event.group_id)
    lastmonth = event.time - last_sent_time_filter
    protect_set = protect_list.get(group_id, set())
    member_list = [member for member in member_list if
                   member['user_id'] not in protect_set and member["last_sent_time"] > lastmonth]
    member_list.sort(key=lambda x: x["last_sent_time"], reverse=True)
    msg = "卡池：\n——————————————\n"
    msg += "\n".join([(member['card'] or member['nickname']) for member in member_list[:80]])
    msg_list.append({"type": "node",
                     "data": {
                         "name": "卡池",
                         "uin": event.self_id,
                         "content": MessageSegment.image(text_to_png(msg))}})

    # 输出透群友记录

    record = [((member['card'] or member['nickname']), times) for member in member_list if
              (times := record_yinpa1.get(member['user_id']))]
    record.sort(key=lambda x: x[1], reverse=True)
    msg = "\n".join(
        [f"[align=left]{nickname}[/align][align=right]今日透群友 {times} 次[/align]" for nickname, times in record])
    if msg:
        msg_list.append({"type": "node",
                         "data": {
                             "name": "记录①",
                             "uin": event.self_id,
                             "content": MessageSegment.image(bbcode_to_png("涩涩记录①：\n——————————————\n" + msg))}})

    # 输出被透记录

    record = [((member['card'] or member['nickname']), times) for member in member_list if
              (times := record_yinpa2.get(member['user_id']))]
    record.sort(key=lambda x: x[1], reverse=True)

    msg = "涩涩记录②：\n——————————————\n"
    msg = "\n".join(
        [f"[align=left]{nickname}[/align][align=right]今日被透 {times} 次[/align]" for nickname, times in record])
    if msg:
        msg_list.append({"type": "node",
                         "data": {
                             "name": "记录②",
                             "uin": event.self_id,
                             "content": MessageSegment.image(bbcode_to_png("涩涩记录②：\n——————————————\n" + msg))}})

    await bot.send_group_forward_msg(group_id=event.group_id, messages=msg_list)
    await yinpa_list.finish()
