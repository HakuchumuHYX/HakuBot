# __init__.py
import asyncio
import re
from typing import Optional
from nonebot import on_message, on_command, get_bot  # 确保 get_bot 被导入（虽然我们用依赖注入）
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, Bot  # 导入 Bot
from nonebot.log import logger
from nonebot.params import CommandArg

from ..utils.common import *
from ..utils.image_utils import path_to_base64_image
from ..plugin_manager.enable import *
from ..plugin_manager.cd_manager import check_cd, update_cd

from .send import load_sticker_list, get_random_sticker, get_random_stickers, resolve_folder_name
from .contribution import extract_contribution_info, save_contribution_images
from .statistics import handle_statistics_command, get_sticker_statistics, render_stickers_preview
from .manage import handle_manage_command, is_superuser
from .check import (
    find_all_duplicates,
    remove_duplicates,
    render_cleanup_report,
    preview_duplicates_before_cleanup,
    safe_remove_duplicates,
    batch_rename_stickers
)
# 导入 help
from . import help
from . import overview

load_sticker_list()

sticker_matcher = on_message(priority=10, block=False)
clean_confirm_matcher = on_command("确认清理", block=True)
clean_cancel_matcher = on_command("取消", block=True)

cleanup_state = {}

RANDOM_ALL_ALIASES = {"stickers", "sticker", "表情", "表情包"}


def parse_multi_random_command(message_text: str) -> tuple[str, int] | None:
    """
    解析多图随机命令（支持多种分隔符）

    返回: (文件夹名, 图片数量) 或 None
    """
    # 匹配格式：随机文件夹名[分隔符]数量
    # 支持的分隔符：x, ×, *, 乘, 乘以
    pattern = r'^随机(\S+?)[\s]*([x×*乘]|乘以)[\s]*(\d+)$'
    match = re.match(pattern, message_text.strip(), re.IGNORECASE)
    if match:
        folder_name = match.group(1).strip()

        if folder_name.lower() in RANDOM_ALL_ALIASES:
            folder_name = "stickers"  # 标准化为 "stickers" 关键字


        try:
            count = int(match.group(3))
            # 限制数量在1-5之间
            count = max(1, min(count, 5))
            return folder_name, count
        except ValueError:
            pass
    return None


async def handle_clean_duplicates_command(event: GroupMessageEvent) -> Optional[str]:
    """
    处理清除重复命令（安全版本）
    """
    message_text = event.get_plaintext().strip()

    if message_text == "清除重复":
        # 检查权限
        if not is_superuser(str(event.user_id)):
            return "权限不足，只有超级用户才能清除重复图片"

        all_duplicates = await find_all_duplicates()

        if not all_duplicates:
            # 即使没有重复，也执行批量重命名
            rename_count, rename_msg = await batch_rename_stickers()
            logger.info(f"无重复图片，自动重命名: {rename_msg}")
            if rename_count > 0:
                return f"未检测到重复图片\n📝 自动重命名完成：{rename_msg}"
            return "未检测到重复图片"

        preview_bytes = await preview_duplicates_before_cleanup(all_duplicates)
        if preview_bytes:
            await sticker_matcher.send(
                MessageSegment.image(preview_bytes) + "\n请回复『确认清理』来执行清理操作，或者回复『取消』取消操作")
        else:
            total_pairs = sum(len(duplicates) for duplicates in all_duplicates.values())
            await sticker_matcher.send(
                f"检测到 {total_pairs} 组重复图片。请回复『确认清理』来执行清理操作，或者回复『取消』取消操作")

        cleanup_state[event.group_id] = {
            'user_id': event.user_id,
            'duplicates': all_duplicates,
            'timestamp': asyncio.get_event_loop().time()
        }

        return "已发送预览，请确认是否继续"

    return None


@clean_confirm_matcher.handle()
async def handle_clean_confirm(event: GroupMessageEvent):
    """处理确认清理命令"""
    if not is_plugin_enabled("stickers", str(event.group_id), str(event.user_id)):
        return
    group_id = event.group_id
    user_id = event.user_id

    if group_id not in cleanup_state:
        await clean_confirm_matcher.finish("没有待处理的清理任务")

    state = cleanup_state[group_id]
    if state['user_id'] != user_id:
        await clean_confirm_matcher.finish("这不是您的清理任务")

    if asyncio.get_event_loop().time() - state['timestamp'] > 300:
        del cleanup_state[group_id]
        await clean_confirm_matcher.finish("清理任务已超时，请重新发起")

    # 执行清理
    removed_count, removed_files = await safe_remove_duplicates(state['duplicates'])
    
    # 清理完成后自动执行批量重命名
    rename_count, rename_msg = await batch_rename_stickers()
    logger.info(f"清理后自动重命名: {rename_msg}")
    
    report_bytes = await render_cleanup_report(removed_count, state['duplicates'])

    # 清理状态（在发送消息前）
    del cleanup_state[group_id]

    # 构建完整的结果消息
    if report_bytes:
        await clean_confirm_matcher.send(MessageSegment.image(report_bytes))
        # 发送重命名结果
        if rename_count > 0:
            await clean_confirm_matcher.finish(f"📝 自动重命名完成：{rename_msg}")
        else:
            await clean_confirm_matcher.finish()
    else:
        total_pairs = sum(len(duplicates) for duplicates in state['duplicates'].values())
        result_msg = f"安全清理完成！检测到{total_pairs}组重复，已移动{removed_count}张图片到备份文件夹"
        if rename_count > 0:
            result_msg += f"\n📝 自动重命名完成：{rename_msg}"
        await clean_confirm_matcher.finish(result_msg)


@clean_cancel_matcher.handle()
async def handle_clean_cancel(event: GroupMessageEvent):
    """处理取消清理命令"""
    if not is_plugin_enabled("stickers", str(event.group_id), str(event.user_id)):
        return
    group_id = event.group_id
    user_id = event.user_id

    if group_id not in cleanup_state:
        await clean_cancel_matcher.finish("没有待处理的清理任务")

    state = cleanup_state[group_id]
    if state['user_id'] != user_id:
        await clean_cancel_matcher.finish("这不是您的清理任务")

    del cleanup_state[group_id]
    await clean_cancel_matcher.finish("已取消清理操作")


# vvvvvv 【修改：添加 Bot 对象】 vvvvvv
@sticker_matcher.handle()
async def handle_sticker(bot: Bot, event: GroupMessageEvent):
    # ^^^^^^ 【修改：添加 Bot 对象】 ^^^^^^
    # 只处理群聊消息
    if not isinstance(event, GroupMessageEvent):
        return
    user_id = str(event.user_id)
    # 检查插件总开关
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("stickers", str(event.group_id), user_id):
            return

    # 获取纯文本消息
    message_text = event.get_plaintext().strip()
    if not message_text:
        return

    # 检查是否是清除重复命令 (SU
    clean_reply = await handle_clean_duplicates_command(event)
    if clean_reply is not None:
        await sticker_matcher.finish(clean_reply)

    # 检查是否是管理命令 (SU
    manage_reply = await handle_manage_command(message_text, event)
    if manage_reply is not None:
        await sticker_matcher.finish(manage_reply)

    # 检查是否是查看统计命令
    if handle_statistics_command(message_text):
        # 渲染贴图预览图片
        try:
            pic_bytes = await render_stickers_preview()
            if pic_bytes:
                await sticker_matcher.send(MessageSegment.image(pic_bytes))
                return  # 使用 return 而不是 finish
        except Exception as e:
            logger.error(f"生成或发送贴图预览图片失败: {e}")

        # 如果图片生成或发送失败，使用文本统计
        statistics_info = get_sticker_statistics()
        await sticker_matcher.finish(statistics_info)

    # 检查是否是投稿格式
    folder_name, is_contribution, is_force = extract_contribution_info(message_text)
    if is_contribution:
        # vvvvvv 【修改：传递 Bot 对象】 vvvvvv
        # 处理投稿
        success, reply_msg, saved_count = await save_contribution_images(bot, folder_name, event, is_force)
        # ^^^^^^ 【修改：传递 Bot 对象】 ^^^^^^

        if success or saved_count == 0:  # 成功或完全失败时回复
            await sticker_matcher.finish(reply_msg)
        return

    # 检查是否是单图随机命令
    if message_text.startswith("随机"):
        PLUGIN_ID_RANDOM = "stickers"  # 使用插件主ID
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        remaining_cd = check_cd(PLUGIN_ID_RANDOM, group_id, user_id)
        if remaining_cd > 0:
            # 冷却中，静默处理，不回复
            return

        # 先检查是否为多图随机命令
        multi_random_result = parse_multi_random_command(message_text)
        if multi_random_result:
            folder_name, count = multi_random_result
            sticker_files = get_random_stickers(folder_name, count)
            if sticker_files:
                # 发送多张图片
                try:
                    # 创建包含多张图片的消息
                    message_segments = []
                    for sticker_file in sticker_files:
                        message_segments.append(path_to_base64_image(sticker_file))

                    update_cd(PLUGIN_ID_RANDOM, group_id, user_id)  # 成功则更新CD

                    await sticker_matcher.finish(Message(message_segments))
                except Exception as e:
                    logger.error(f"发送多图随机贴图失败: {e}")
            return

        # 如果不是多图随机命令，处理单图随机命令
        # 提取文件夹名（去掉"随机"前缀）
        folder_name = message_text[2:].strip()

        # 检查是否为“随机所有”的别名
        if folder_name.lower() in RANDOM_ALL_ALIASES:
            folder_name = "stickers"  # 标准化为 "stickers" 关键字

        if folder_name:
            # 使用支持别名的函数获取贴图
            sticker_file = get_random_sticker(folder_name)
            if sticker_file:
                # 发送图片
                try:
                    update_cd(PLUGIN_ID_RANDOM, group_id, user_id)  # 成功则更新CD

                    await sticker_matcher.finish(path_to_base64_image(sticker_file))
                except Exception as e:
                    logger.error(f"发送随机贴图失败: {e}")
