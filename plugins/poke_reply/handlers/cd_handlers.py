# handlers/cd_handlers.py
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent, Message, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.params import CommandArg
from nonebot.exception import FinishedException

from ..config import (
    add_poke_cd_group,
    remove_poke_cd_group,
    is_poke_cd_enabled,
    set_poke_cd_time,
    get_poke_cd_time,
    get_poke_cd_enabled_groups,
    get_text_to_image_enabled_groups,
    is_text_to_image_enabled,
    get_text_to_image_threshold
)

# 注册CD管理命令处理器
enable_poke_cd = on_command("启用戳一戳CD", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
disable_poke_cd = on_command("禁用戳一戳CD", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
poke_cd_status = on_command("戳一戳CD状态", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
set_poke_cd_time_cmd = on_command("设置戳一戳CD", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_all_cd_groups = on_command("查看CD群组", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_all_text_to_image_groups = on_command("查看文本转图片群组", permission=SUPERUSER, rule=to_me(), priority=5,
                                           block=True)  # 新增


@enable_poke_cd.handle()
async def handle_enable_poke_cd(event: GroupMessageEvent, args: Message = CommandArg()):
    """启用戳一戳CD功能"""
    group_id = event.group_id
    arg_text = args.extract_plain_text().strip()

    try:
        if arg_text:
            # 如果指定了CD时间，更新CD时间
            new_cd_time = int(arg_text)
            set_poke_cd_time(new_cd_time)
            add_poke_cd_group(group_id)
            await enable_poke_cd.finish(f"已启用戳一戳CD功能，CD时间设置为 {new_cd_time} 秒喵！")
        else:
            add_poke_cd_group(group_id)
            await enable_poke_cd.finish(
                f"已启用戳一戳CD功能，当前CD时间为 {get_poke_cd_time()} 秒喵！")

    except ValueError:
        await enable_poke_cd.finish("CD时间必须是数字喵！")


@disable_poke_cd.handle()
async def handle_disable_poke_cd(event: GroupMessageEvent):
    """禁用戳一戳CD功能"""
    group_id = event.group_id
    remove_poke_cd_group(group_id)
    await disable_poke_cd.finish("已禁用戳一戳CD功能喵！")


@poke_cd_status.handle()
async def handle_poke_cd_status(event: MessageEvent):
    """查看戳一戳CD状态"""
    cd_time = get_poke_cd_time()

    if isinstance(event, GroupMessageEvent):
        # 群聊中显示当前群状态
        group_id = event.group_id
        enabled = is_poke_cd_enabled(group_id)

        status_msg = "启用" if enabled else "禁用"
        message = (
            f"戳一戳CD功能状态：{status_msg}\n"
            f"当前CD时间：{cd_time} 秒\n"
            f"生效范围：仅对非管理员用户生效"
        )
    else:
        # 私聊中显示所有启用CD的群组
        enabled_groups = get_poke_cd_enabled_groups()

        if not enabled_groups:
            message = "当前没有群组启用戳一戳CD功能喵！"
        else:
            message = f"📋 启用戳一戳CD的群组 (CD时间: {cd_time}秒):\n\n"
            for i, group_id in enumerate(enabled_groups, 1):
                message += f"{i}. 群组ID: {group_id}\n"

            message += f"\n总计: {len(enabled_groups)} 个群组"

    await poke_cd_status.finish(message)


@set_poke_cd_time_cmd.handle()
async def handle_set_poke_cd_time(event: GroupMessageEvent, args: Message = CommandArg()):
    """设置戳一戳CD时间"""
    arg_text = args.extract_plain_text().strip()

    try:
        if not arg_text:
            await set_poke_cd_time_cmd.finish(f"当前戳一戳CD时间为 {get_poke_cd_time()} 秒喵！")
            return

        new_cd_time = int(arg_text)
        if new_cd_time < 5:
            await set_poke_cd_time_cmd.finish("CD时间不能小于5秒喵！")
            return

        set_poke_cd_time(new_cd_time)
        await set_poke_cd_time_cmd.finish(f"已设置戳一戳CD时间为 {new_cd_time} 秒喵！")

    except ValueError:
        await set_poke_cd_time_cmd.finish("CD时间必须是数字喵！")


@view_all_cd_groups.handle()
async def handle_view_all_cd_groups(event: PrivateMessageEvent):
    """查看所有启用戳一戳CD的群组"""
    try:
        enabled_groups = get_poke_cd_enabled_groups()
        cd_time = get_poke_cd_time()

        if not enabled_groups:
            await view_all_cd_groups.finish("当前没有群组启用戳一戳CD功能喵！")
            return

        message = f"📋 启用戳一戳CD的群组 (CD时间: {cd_time}秒):\n\n"
        for i, group_id in enumerate(enabled_groups, 1):
            message += f"{i}. 群组ID: {group_id}\n"

        message += f"\n总计: {len(enabled_groups)} 个群组"

        await view_all_cd_groups.finish(message)
        
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise

    except Exception as e:
        logger.error(f"查看CD群组时出错: {e}")
        await view_all_cd_groups.finish("获取CD群组列表失败喵！")


@view_all_text_to_image_groups.handle()
async def handle_view_all_text_to_image_groups(event: PrivateMessageEvent):
    """查看所有启用文本转图片的群组"""
    try:
        enabled_groups = get_text_to_image_enabled_groups()
        threshold = get_text_to_image_threshold()

        if not enabled_groups:
            await view_all_text_to_image_groups.finish("当前没有群组启用文本转图片功能喵！")
            return

        message = f"📋 启用文本转图片的群组 (阈值: {threshold}字符):\n\n"
        for i, group_id in enumerate(enabled_groups, 1):
            message += f"{i}. 群组ID: {group_id}\n"

        message += f"\n总计: {len(enabled_groups)} 个群组"

        await view_all_text_to_image_groups.finish(message)
        
    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"查看文本转图片群组时出错: {e}")
        await view_all_text_to_image_groups.finish("获取文本转图片群组列表失败喵！")