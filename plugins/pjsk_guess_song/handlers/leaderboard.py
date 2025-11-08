# pjsk_guess_song/handlers/leaderboard.py
"""
(新文件)
存放排行榜指令
"""
from pathlib import Path
from nonebot import on_command
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment, Bot, GroupMessageEvent

# [重构] 导入 db_service 和 image_service
from .. import db_service, image_service
from ...plugin_manager.enable import is_plugin_enabled
from ...utils.common import create_exact_command_rule

leaderboard_handler = on_command("群聊猜歌排行",
                                 aliases={"猜歌排行", "pjsk排行"},
                                 priority=10,
                                 block=True,
                                 rule=create_exact_command_rule("群聊猜歌排行", {"猜歌排行", "pjsk排行"})
                                 )


@leaderboard_handler.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher):
    user_id = str(event.user_id)
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("...此功能仅限群聊使用。")

    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("pjsk_guess_song", str(event.group_id), user_id):
            await leaderboard_handler.finish("猜歌功能在此群无法使用！")
            return

    group_id = str(event.group_id)

    try:
        # 1. 从数据库获取排行数据
        leaderboard_data = await db_service.get_group_leaderboard(group_id, limit=5)

        if not leaderboard_data:
            await matcher.finish("本群还没有人猜对歌曲哦，快来玩“猜歌”吧！")

        try:
            group_info = await bot.get_group_info(group_id=event.group_id)
            group_name = group_info.get('group_name', f"群 {group_id}")
        except Exception:
            group_name = f"群 {group_id}"

        # 2. [重构] 调用 image_service 绘制图片
        img_path = await image_service.draw_leaderboard_image(group_name, leaderboard_data)

        if img_path:
            img_p = Path(img_path)
            await matcher.send(MessageSegment.image(file=img_p.absolute().as_uri()))
        else:
            # 绘图失败，回退到文本
            await matcher.send("...排行榜图片生成失败，即将发送文本版：\n" + \
                               _format_leaderboard_text(group_name, leaderboard_data))

    except Exception as e:
        logger.error(f"获取或绘制排行榜失败: {e}", exc_info=True)
        await matcher.send("...获取排行榜时出错，请联系管理员。")


def _format_leaderboard_text(group_name: str, data: list) -> str:
    """纯文本排行榜的后备方案"""
    lines = [f"--- {group_name} 猜歌排行 ---"]
    for i, (name, score) in enumerate(data, 1):
        lines.append(f"No.{i} {name} - {score} 分")
    return "\n".join(lines)