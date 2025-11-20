import random
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.rule import Rule

# --- 导入管理插件 API ---
# 注意：这里的导入路径取决于你的文件夹结构
# 如果报错 ImportError，请根据实际位置调整点号数量 (例如 .plugin_manager 或 ..plugin_manager)
from ..plugin_manager.enable import is_plugin_enabled
from ..plugin_manager.cd_manager import check_cd, update_cd

# --- 配置 ---
PLUGIN_ID = "hyw"
KEYWORDS = {"？", "?", "啥意思", "何意味", "hyw", "何异味"}


# --- 逻辑 1: 关键词触发 ---
async def keyword_checker(event: GroupMessageEvent) -> bool:
    return event.get_plaintext().strip() in KEYWORDS

heyiwei = on_message(rule=Rule(keyword_checker), priority=10, block=False)


@heyiwei.handle()
async def handle_heyiwei(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查总开关
    if not is_plugin_enabled(PLUGIN_ID, group_id, user_id):
        return

    # 2. 检查CD & 更新
    if check_cd(PLUGIN_ID, group_id, user_id) > 0:
        return
    update_cd(PLUGIN_ID, group_id, user_id)

    # 3. 回复消息 (重点修改：添加 reply_message=True)
    await heyiwei.finish("何意味", reply_message=True)


# --- 逻辑 2: 彩蛋触发 (千分之一) ---
async def random_checker(event: GroupMessageEvent) -> bool:
    # 概率判定：1/500
    return random.randint(1, 500) == 1

# 优先级设为 99，不阻断
easter_egg = on_message(rule=Rule(random_checker), priority=99, block=False)


@easter_egg.handle()
async def handle_egg(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    # 1. 检查总开关 (和上面用同一个 PLUGIN_ID)
    if not is_plugin_enabled(PLUGIN_ID, group_id, user_id):
        return

    # 2. 检查 CD (和主功能共享 CD，防止刷屏)
    if check_cd(PLUGIN_ID, group_id, user_id) > 0:
        return

    # 3. 更新 CD 并发送
    update_cd(PLUGIN_ID, group_id, user_id)

    # 4. 回复消息 (重点修改：添加 reply_message=True)
    await easter_egg.finish("何意味", reply_message=True)
