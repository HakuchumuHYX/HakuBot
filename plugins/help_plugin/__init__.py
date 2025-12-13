from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment, Message
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from .manager import help_manager

driver = get_driver()

help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)
reload_cmd = on_command("reload_help", aliases={"重载帮助"}, priority=1, block=True)


# --- 启动时预热 ---
@driver.on_startup
async def _():
    # 记得加 await
    await help_manager.get_help_data()


# --- 帮助命令 ---
@help_cmd.handle()
async def handle_help(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    try:
        img_path, links = await help_manager.get_help_data(force_update=False)

        await matcher.send(MessageSegment.image(img_path))

        if links:
            forward_nodes = []
            forward_nodes.append(
                MessageSegment.node_custom(
                    user_id=bot.self_id,
                    nickname="ATRI",
                    content=Message("包含的链接如下：")
                )
            )
            for index, link in enumerate(links, 1):
                forward_nodes.append(
                    MessageSegment.node_custom(
                        user_id=bot.self_id,
                        nickname="ATRI",
                        content=Message(f"{index}. {link}")
                    )
                )

            await bot.call_api(
                "send_group_forward_msg",
                group_id=event.group_id,
                messages=forward_nodes
            )

    except FinishedException:
        raise
    except Exception as e:
        await matcher.finish(f"发送帮助信息失败：{e}")


# --- 重载命令 ---
@reload_cmd.handle()
async def handle_reload(matcher: Matcher):
    try:
        # 记得加 await
        await help_manager.get_help_data(force_update=True)
        await matcher.finish("帮助文本及图片缓存已强制重载！")
    except FinishedException:
        raise
    except Exception as e:
        await matcher.finish(f"重载失败：{e}")