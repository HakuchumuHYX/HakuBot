import nonebot
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, Message, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.log import logger
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

# 插件数据目录
plugin_dir = Path(__file__).parent
data_file = plugin_dir / "bind.json"

# 存储绑定数据的内存缓存
bind_data: Dict[str, str] = {}


# 初始化：加载现有绑定数据
def load_bind_data():
    global bind_data
    try:
        if data_file.exists():
            with open(data_file, 'r', encoding='utf-8') as f:
                bind_data = json.load(f)
            logger.success(f"已加载 {len(bind_data)} 条绑定记录")
        else:
            bind_data = {}
            logger.info("绑定数据文件不存在，将创建新文件")
    except Exception as e:
        logger.error(f"加载绑定数据失败: {e}")
        bind_data = {}


# 保存绑定数据到文件
def save_bind_data():
    try:
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(bind_data, f, ensure_ascii=False, indent=2)
        logger.debug("绑定数据已保存")
    except Exception as e:
        logger.error(f"保存绑定数据失败: {e}")
        return False
    return True


# 初始化加载数据
load_bind_data()

# 绑定命令
bind_cmd = on_command("buaa绑定", priority=5, block=True)


@bind_cmd.handle()
async def handle_bind_command(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    # 获取参数
    bind_content = args.extract_plain_text().strip()

    # 检查参数是否为空
    if not bind_content:
        await bind_cmd.finish("请提供要绑定的内容，格式：buaa绑定+xxxx")
        return

    user_id = str(event.user_id)

    # 执行绑定操作
    old_content = bind_data.get(user_id)
    bind_data[user_id] = bind_content

    # 保存到文件
    if save_bind_data():
        # 回复用户
        if old_content:
            await bind_cmd.send(f"绑定成功！\n已更新绑定内容：\n原内容：{old_content}\n新内容：{bind_content}")
        else:
            await bind_cmd.send(f"绑定成功！\nQQ：{user_id}\n绑定内容：{bind_content}")
    else:
        # 如果保存失败，回滚内存中的数据
        if old_content:
            bind_data[user_id] = old_content
        else:
            bind_data.pop(user_id, None)
        await bind_cmd.send("绑定失败，数据保存出错，请稍后重试")


# 处理群聊中的绑定命令
@bind_cmd.handle()
async def handle_group_bind_command(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    await bind_cmd.finish("该指令仅在私聊中可用")


# 查询绑定命令
query_bind = on_command("buaa查询绑定", priority=5, block=True)


@query_bind.handle()
async def handle_query_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    if user_id in bind_data:
        content = bind_data[user_id]
        await query_bind.finish(f"您的绑定信息：\nQQ：{user_id}\n绑定内容：{content}")
    else:
        await query_bind.finish("您尚未绑定任何内容")


# 处理群聊中的查询绑定命令
@query_bind.handle()
async def handle_group_query_command(bot: Bot, event: GroupMessageEvent):
    await query_bind.finish("该指令仅在私聊中可用")


# 解除绑定命令
unbind_cmd = on_command("buaa解除绑定", priority=5, block=True)


@unbind_cmd.handle()
async def handle_unbind_command(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)

    if user_id in bind_data:
        old_content = bind_data[user_id]
        del bind_data[user_id]
        if save_bind_data():
            await unbind_cmd.finish(f"已解除绑定\n原绑定内容：{old_content}")
        else:
            # 如果保存失败，恢复数据
            bind_data[user_id] = old_content
            await unbind_cmd.finish("解除绑定失败，数据保存出错")
    else:
        await unbind_cmd.finish("您尚未绑定任何内容，无需解除")


# 处理群聊中的解除绑定命令
@unbind_cmd.handle()
async def handle_group_unbind_command(bot: Bot, event: GroupMessageEvent):
    await unbind_cmd.finish("该指令仅在私聊中可用")


# 管理员查看所有绑定
view_all_binds = on_command("buaa查看所有绑定", priority=10, block=True)


@view_all_binds.handle()
async def handle_view_all_command(bot: Bot, event: PrivateMessageEvent):
    # 检查是否是超级用户
    superusers = get_driver().config.superusers
    if str(event.user_id) not in superusers:
        await view_all_binds.finish("权限不足，仅超级用户可使用此命令")
        return

    if not bind_data:
        await view_all_binds.finish("当前没有任何绑定记录")
        return

    # 格式化绑定信息
    bind_list = []
    for qq, content in bind_data.items():
        bind_list.append(f"QQ：{qq} -> 内容：{content}")

    result = "当前所有绑定记录：\n" + "\n".join(bind_list)

    # 如果消息过长，分条发送
    if len(result) > 500:
        parts = [result[i:i + 500] for i in range(0, len(result), 500)]
        for part in parts:
            await bot.send_private_msg(user_id=event.user_id, message=part)
        await view_all_binds.finish("以上是所有绑定记录")
    else:
        await view_all_binds.finish(result)


# 处理群聊中的查看所有绑定命令
@view_all_binds.handle()
async def handle_group_view_all_command(bot: Bot, event: GroupMessageEvent):
    await view_all_binds.finish("该指令仅在私聊中可用")


# 插件加载成功提示
logger.success("好友绑定插件加载成功")