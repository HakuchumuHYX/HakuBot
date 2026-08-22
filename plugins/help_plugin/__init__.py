from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher

from .manager import help_manager
from ..plugin_manager.enable import is_plugin_enabled
from ..utils.image_utils import image_segment
from ..utils.tools import send_forward_msg

driver = get_driver()

help_cmd = on_command("help", aliases={"帮助", "菜单"}, priority=5, block=True)
reload_cmd = on_command("reload_help", aliases={"重载帮助"}, priority=1, block=True)


@driver.on_startup
async def _():
    await help_manager.get_help_data()


@help_cmd.handle()
async def handle_help(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    # 检查插件是否启用
    user_id = str(event.user_id)
    if not is_plugin_enabled("help_plugin", str(event.group_id), user_id):
        return
    
    try:
        img_path, links = await help_manager.get_help_data(force_update=False)

        await matcher.send(image_segment(img_path))

        if links:
            forward_nodes = ["包含的链接如下："]
            for index, link in enumerate(links, 1):
                forward_nodes.append(f"{index}. {link}")

            await send_forward_msg(bot, group_id=event.group_id, items=forward_nodes)

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
