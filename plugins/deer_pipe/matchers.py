"""deer_pipe 插件命令处理器模块"""

from datetime import datetime

from nonebot import logger
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.exception import FinishedException
from nonebot_plugin_alconna import Alconna, AlconnaMatcher, Args, Match, on_alconna
from nonebot_plugin_alconna.uniseg import At, UniMessage
from nonebot_plugin_userinfo import EventUserInfo, UserInfo

from core.access import is_plugin_enabled
from core.cooldown import check_cd, update_cd
from plugins.deer_pipe.config import config
from plugins.deer_pipe.constants import PLUGIN_ID
from plugins.deer_pipe.database import (
    attend,
    attend_past,
    get_avatar,
    get_deer_map,
    update_avatar,
)
from plugins.deer_pipe.image import render_calendar
from utils.onebot.media import prepare_image_source

# ==================== 命令定义 ====================

# 签到命令
deer: AlconnaMatcher = on_alconna(
    Alconna("🦌", Args["target?", At]),
    aliases={"鹿"},
    priority=5,
    block=True,
)

# 补签命令
deer_past: AlconnaMatcher = on_alconna(
    Alconna("补🦌", Args["day", int]),
    aliases={"补鹿"},
    priority=5,
    block=True,
)

# 查看日历命令
deer_calendar: AlconnaMatcher = on_alconna(
    Alconna("🦌历", Args["target?", At]),
    aliases={"鹿历"},
    priority=5,
    block=True,
)


# ==================== 命令处理器 ====================


@deer.handle()
async def handle_deer(
    event: Event,
    target: Match[At],
    user_info: UserInfo = EventUserInfo(),
):
    """处理签到命令"""
    try:
        # 检查是否在群聊中
        is_group = isinstance(event, GroupMessageEvent)
        caller_user_id = str(event.get_user_id())

        if is_group:
            group_id = str(event.group_id)

            # 检查插件是否启用
            if not is_plugin_enabled(PLUGIN_ID, group_id, caller_user_id):
                await deer.finish()

            # 检查CD
            remaining_cd = check_cd(PLUGIN_ID, group_id, caller_user_id)
            if remaining_cd > 0:
                await deer.finish(config.cd_message.format(remaining=remaining_cd))

        now = datetime.now()

        # 确定签到目标
        if target.available:
            # 帮他人签到
            if not config.enable_help_deer:
                await deer.finish("帮他人签到功能已禁用")

            target_user_id = target.result.target
            avatar = await get_avatar(target_user_id)
            logger.info(f"用户 {caller_user_id} 帮 {target_user_id} 签到")
        else:
            # 自己签到
            target_user_id = user_info.user_id
            avatar = (
                await user_info.user_avatar.get_image()
                if user_info.user_avatar is not None
                else None
            )
            # 更新头像缓存
            await update_avatar(target_user_id, avatar)
            logger.info(f"用户 {target_user_id} 签到")

        # 执行签到
        deer_map = await attend(target_user_id, now)

        # 生成日历图片
        img = await render_calendar(now, deer_map, avatar)

        # 更新CD（仅在群聊中）
        if is_group:
            update_cd(PLUGIN_ID, group_id, caller_user_id)

        # 发送结果
        if target.available:
            await (
                UniMessage.text("成功帮")
                .at(target_user_id)
                .text("🦌了")
                .image(path=prepare_image_source(img))
                .finish(reply_to=True)
            )
        else:
            await (
                UniMessage.text(config.success_message)
                .image(path=prepare_image_source(img))
                .finish(reply_to=True)
            )

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"签到处理失败: {e}")
        await deer.finish("签到失败，请稍后重试")


@deer_past.handle()
async def handle_deer_past(
    event: Event,
    day: Match[int],
    user_info: UserInfo = EventUserInfo(),
):
    """处理补签命令"""
    try:
        # 检查补签功能是否启用
        if not config.enable_past_deer:
            await deer_past.finish("补签功能已禁用")

        # 检查是否在群聊中
        is_group = isinstance(event, GroupMessageEvent)
        caller_user_id = str(event.get_user_id())

        if is_group:
            group_id = str(event.group_id)

            # 检查插件是否启用
            if not is_plugin_enabled(PLUGIN_ID, group_id, caller_user_id):
                await deer_past.finish()

            # 检查CD
            remaining_cd = check_cd(PLUGIN_ID, group_id, caller_user_id)
            if remaining_cd > 0:
                await deer_past.finish(config.cd_message.format(remaining=remaining_cd))

        now = datetime.now()
        target_day = day.result

        # 验证日期有效性
        if target_day < 1 or target_day >= now.day:
            await deer_past.finish(config.invalid_date_message)

        # 获取用户信息
        target_user_id = user_info.user_id
        avatar = (
            await user_info.user_avatar.get_image()
            if user_info.user_avatar is not None
            else None
        )
        await update_avatar(target_user_id, avatar)

        logger.debug(f"用户 {target_user_id} 尝试补签 {now.month}月{target_day}日")

        # 执行补签
        success, deer_map = await attend_past(target_user_id, now, target_day)

        # 生成日历图片
        img = await render_calendar(now, deer_map, avatar)

        # 补签成功时更新CD
        if success and is_group:
            update_cd(PLUGIN_ID, group_id, caller_user_id)

        # 发送结果
        if success:
            await (
                UniMessage.text(config.past_success_message)
                .image(path=prepare_image_source(img))
                .finish(reply_to=True)
            )
        else:
            await (
                UniMessage.text(config.already_signed_message)
                .image(path=prepare_image_source(img))
                .finish(reply_to=True)
            )

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"补签处理失败: {e}")
        await deer_past.finish("补签失败，请稍后重试")


@deer_calendar.handle()
async def handle_deer_calendar(
    event: Event,
    target: Match[At],
    user_info: UserInfo = EventUserInfo(),
):
    """处理查看日历命令"""
    try:
        # 检查是否在群聊中
        is_group = isinstance(event, GroupMessageEvent)
        caller_user_id = str(event.get_user_id())

        if is_group:
            group_id = str(event.group_id)

            # 检查插件是否启用
            if not is_plugin_enabled(PLUGIN_ID, group_id, caller_user_id):
                await deer_calendar.finish()

        now = datetime.now()

        # 确定查看目标
        if target.available:
            target_user_id = target.result.target
            avatar = await get_avatar(target_user_id)
            logger.debug(f"用户 {caller_user_id} 查看 {target_user_id} 的日历")
        else:
            target_user_id = user_info.user_id
            avatar = (
                await user_info.user_avatar.get_image()
                if user_info.user_avatar is not None
                else None
            )
            await update_avatar(target_user_id, avatar)
            logger.debug(f"用户 {target_user_id} 查看自己的日历")

        # 获取签到记录
        deer_map = await get_deer_map(target_user_id, now)

        # 生成日历图片
        img = await render_calendar(now, deer_map, avatar)

        await UniMessage.image(path=prepare_image_source(img)).finish(reply_to=True)

    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"查看日历失败: {e}")
        await deer_calendar.finish("获取日历失败，请稍后重试")
