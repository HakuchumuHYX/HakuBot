# handlers/stat_handlers.py
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.exception import FinishedException

from ..core.data_manager import data_manager
from ..config import TEXT_FILES_DIR, IMAGE_FILES_DIR

import json
from pathlib import Path
# ... 其余内容保持不变，只需更新导入路径 ...

# 注册命令处理器
view_text_count = on_command("查看文本数", rule=to_me(), priority=5, block=True)
view_all_text_count = on_command("查看所有文本数", permission=SUPERUSER, rule=to_me(), priority=5, block=True)
view_content_stats = on_command("查看投稿统计", rule=to_me(), priority=5, block=True)  # 新增：查看详细统计


@view_text_count.handle()
async def handle_view_text_count(event: MessageEvent):
    """处理查看当前群聊文本数命令"""
    if isinstance(event, PrivateMessageEvent):
        await view_text_count.finish("请在群聊中使用此命令喵！")
        return

    group_id = event.group_id

    # 确保数据已加载
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_text_count.finish("数据加载失败，无法查看文本数喵！")
        return

    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    await view_text_count.finish(f"当前群共有 {text_count} 条文本和 {image_count} 张图片喵！")


@view_content_stats.handle()
async def handle_view_content_stats(event: MessageEvent):
    """处理查看详细投稿统计命令"""
    if isinstance(event, PrivateMessageEvent):
        await view_content_stats.finish("请在群聊中使用此命令喵！")
        return

    group_id = event.group_id

    # 确保数据已加载
    if not data_manager.ensure_group_data_loaded(group_id):
        await view_content_stats.finish("数据加载失败，无法查看统计喵！")
        return

    text_count = data_manager.get_text_count(group_id)
    image_count = data_manager.get_image_count(group_id)
    total_count = text_count + image_count

    if total_count == 0:
        await view_content_stats.finish("当前群还没有任何投稿内容喵！")
        return

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
    """处理查看所有群聊文本数命令（仅超级用户可用）"""
    try:
        # 获取所有群聊的投稿统计
        total_groups = 0
        total_texts = 0
        total_images = 0
        group_details = []

        # 遍历text_files目录下的所有JSON文件
        for file_path in TEXT_FILES_DIR.glob("text_*.json"):
            try:
                # 从文件名提取群号
                filename = file_path.stem  # 去掉扩展名
                group_id = int(filename[5:])  # 去掉"text_"前缀

                # 加载该群组的数据
                if data_manager.ensure_group_data_loaded(group_id):
                    text_count = data_manager.get_text_count(group_id)
                    image_count = data_manager.get_image_count(group_id)
                    total_texts += text_count
                    total_images += image_count
                    total_groups += 1

                    # 记录群组详情
                    if text_count > 0 or image_count > 0:
                        group_details.append(f"群 {group_id}: {text_count}文/{image_count}图")
            except (ValueError, Exception) as e:
                logger.warning(f"处理文件 {file_path} 时出错: {e}")
                continue

        # 构建回复消息
        if total_groups == 0:
            message = "还没有任何群聊有投稿内容喵！"
        else:
            total_content = total_texts + total_images
            message = f"共 {total_groups} 个群聊有投稿，总计 {total_content} 个内容喵！\n"
            message += f"📝 文本: {total_texts} 条\n"
            message += f"🖼️  图片: {total_images} 张\n\n"

            # 如果群组数量不多，显示详情
            if len(group_details) <= 10:
                message += "各群详情:\n" + "\n".join(group_details)
            else:
                # 只显示前10个群组
                message += "前10个群组详情:\n" + "\n".join(group_details[:10])
                message += f"\n... 还有 {len(group_details) - 10} 个群聊"

        # 发送结果并结束处理
        await view_all_text_count.finish(message)

    except FinishedException:
        # 忽略FinishedException，这是正常的结束流程
        pass
    except Exception as e:
        logger.error(f"获取所有群聊投稿统计时出错: {e}")
        await view_all_text_count.finish("获取统计信息时出错喵！")