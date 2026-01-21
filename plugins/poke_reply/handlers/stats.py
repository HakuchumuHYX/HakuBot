from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    PrivateMessageEvent,
    MessageEvent,
    Bot
)
from nonebot.rule import to_me
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

from ..models.data import data_manager
from ..services import image as image_service
from ..config import TEXT_FILES_DIR

# --- 统计命令 ---
view_text_count = on_command("查看投稿数", rule=to_me(), priority=5, block=True)
view_all_text_count = on_command("查看所有投稿数", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_content_stats = on_command("查看投稿统计", rule=to_me(), priority=5, block=True)
clear_duplicates = on_command("清除投稿重复", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


@view_text_count.handle()
async def handle_view_text_count(event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await view_text_count.finish("请在群聊中使用此命令喵！")
    group_id = event.group_id
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_text_count.finish("数据加载失败，无法查看文本数喵！")
    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    await view_text_count.finish(f"当前群共有 {text_count} 条文本和 {image_count} 张图片喵！")


@view_content_stats.handle()
async def handle_view_content_stats(event: MessageEvent):
    if isinstance(event, PrivateMessageEvent):
        await view_content_stats.finish("请在群聊中使用此命令喵！")
    group_id = event.group_id
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_content_stats.finish("数据加载失败，无法查看统计喵！")
    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    total_count = text_count + image_count
    if total_count == 0:
        await view_content_stats.finish("当前群还没有任何投稿内容喵！")
    text_ratio = (text_count / total_count) * 100
    image_ratio = (image_count / total_count) * 100
    message = (
        f"📊 投稿统计详情：\n"
        f"📝 文本数量：{text_count} 条 ({text_ratio:.1f}%)\n"
        f"🖼️  图片数量：{image_count} 张 ({image_ratio:.1f}%)\n"
        f"📦 总计：{total_count} 个内容\n\n"
        f"戳一戳时：\n"
        f"• 文本发送概率：{text_ratio:.1f}%\n"
        f"• 图片发送概率：{image_ratio:.1f}%"
    )
    await view_content_stats.finish(message)


@view_all_text_count.handle()
async def handle_view_all_text_count(event: MessageEvent):
    try:
        total_groups = 0
        total_texts = 0
        total_images = 0
        group_details = []
        # 使用 GLOB 查找所有文本数据文件来统计
        for file_path in TEXT_FILES_DIR.glob("text_*.json"):
            try:
                filename = file_path.stem
                # 文件名格式为 text_123456.json
                group_id = int(filename[5:])
                if data_manager.ensure_group_data_loaded(group_id):
                    text_count = data_manager.get_text_count(group_id)
                    image_count = data_manager.get_image_count(group_id)
                    total_texts += text_count
                    total_images += image_count
                    total_groups += 1
                    if text_count > 0 or image_count > 0:
                        group_details.append(f"群 {group_id}: {text_count}文/{image_count}图")
            except (ValueError, Exception) as e:
                logger.warning(f"处理文件 {file_path} 时出错: {e}")
        
        if total_groups == 0:
            message = "还没有任何群聊有投稿内容喵！"
        else:
            total_content = total_texts + total_images
            message = f"共 {total_groups} 个群聊有投稿，总计 {total_content} 个内容喵！\n"
            message += f"📝 文本: {total_texts} 条\n"
            message += f"🖼️  图片: {total_images} 张\n\n"
            if len(group_details) <= 10:
                message += "各群详情:\n" + "\n".join(group_details)
            else:
                message += "前10个群组详情:\n" + "\n".join(group_details[:10])
                message += f"\n... 还有 {len(group_details) - 10} 个群聊"
        await view_all_text_count.finish(message)
    except Exception as e:
        logger.error(f"获取所有群聊投稿统计时出错: {e}")
        await view_all_text_count.finish("获取统计信息时出错喵！")


@clear_duplicates.handle()
async def handle_clear_duplicates(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id

    await clear_duplicates.send("正在开始检查本群所有图片，这可能需要几分钟，请稍候...")

    try:
        # 1. 查找重复
        duplicates_found = await image_service.find_group_duplicates(group_id)

        if not duplicates_found:
            await clear_duplicates.finish("检查完毕，未在本群发现重复的图片喵！")

        num_pairs = len(duplicates_found)

        # 2. 删除重复
        removed_count = image_service.safe_remove_group_duplicates(group_id, duplicates_found)

        await clear_duplicates.finish(
            f"清理完成！\n"
            f"共发现 {num_pairs} 组重复图片。\n"
            f"成功删除了 {removed_count} 张多余的图片文件。"
        )

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"执行清除重复命令时出错: {e}")
        await clear_duplicates.finish(f"清理过程中发生错误: {e}")
