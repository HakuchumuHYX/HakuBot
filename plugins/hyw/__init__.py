import random
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

# --- 导入管理插件 API ---
from ..plugin_manager.enable import is_plugin_enabled
from ..plugin_manager.cd_manager import check_cd, update_cd

# --- 配置 ---
PLUGIN_ID = "hyw"
KEYWORDS = {"？", "?", "啥意思", "何意味", "hyw", "何异味"}

# 【新增配置】关键词触发的回复概率 (0.1 = 10%, 0.5 = 50%, 1.0 = 100%)
# 只有当你发送 "?" 且命中这 30% 概率时，才会回复
KEYWORD_PROBABILITY = 0.3


# --- 逻辑 1: 关键词触发 ---
async def keyword_checker(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip() in KEYWORDS

heyiwei = on_message(rule=Rule(keyword_checker), priority=10, block=False)


@heyiwei.handle()
async def handle_heyiwei(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查总开关
    if not is_plugin_enabled(PLUGIN_ID, group_id, 0):
        return

    # 2. 检查CD
    # 如果还在CD中，直接退出
    if check_cd(PLUGIN_ID, group_id, user_id) > 0:
        return

    # 3. 【新增】概率检查
    # 如果生成的随机数大于设定的概率，则忽略本次消息
    # 这样就不会每次都回了
    if random.random() > KEYWORD_PROBABILITY:
        return

    # 4. 只有确定要回复了，才开始记录CD
    update_cd(PLUGIN_ID, group_id, user_id)

    # 5. 回复消息 (引用发送者)
    await heyiwei.finish("何意味", reply_message=True)


# --- 逻辑 2: 彩蛋触发 (你设定的 1/500) ---
async def random_checker(event: GroupMessageEvent) -> bool:
    # 概率判定：1/500
    return random.randint(1, 500) == 1

# 优先级设为 99，不阻断
easter_egg = on_message(rule=Rule(random_checker), priority=99, block=False)


@easter_egg.handle()
async def handle_egg(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查总开关
    if not is_plugin_enabled(PLUGIN_ID, group_id, 0):
        return

    # 2. 检查 CD
    if check_cd(PLUGIN_ID, group_id, user_id) > 0:
        return

    # 3. 更新 CD 并发送
    update_cd(PLUGIN_ID, group_id, user_id)

    # 4. 回复消息 (引用发送者)
    await easter_egg.finish("何意味", reply_message=True)
