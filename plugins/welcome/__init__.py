from nonebot import on_notice, require
from nonebot.adapters.onebot.v11 import GroupIncreaseNoticeEvent, Message, MessageSegment
import random
from pathlib import Path
from ..plugin_manager.enable import is_plugin_enabled

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# 获取插件数据目录，用于存放欢迎图片
welcome_dir = store.get_plugin_data_dir()  # 这会在标准数据目录下创建 welcome 文件夹

welcome = on_notice()


@welcome.handle()
async def handle_welcome(event: GroupIncreaseNoticeEvent):
    if event.notice_type != "group_increase":
        return

    group_id = str(event.group_id)
    if not is_plugin_enabled("welcome", group_id, "0"):
        return

    user_id = event.user_id

    # 确保欢迎图片目录存在
    welcome_dir.mkdir(parents=True, exist_ok=True)

    # 获取所有图片文件
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
    image_files = [
        f for f in welcome_dir.iterdir()
        if f.is_file() and f.suffix.lower() in image_extensions
    ]

    if not image_files:
        # 如果没有图片，发送文字欢迎
        welcome_message = Message([
            MessageSegment.at(user_id),
            MessageSegment.text("\n欢迎新人！群高性能萝卜子ATRIだよ~\n发送“help”可获取帮助文档~")
        ])
    else:
        # 随机选择图片
        selected_image = random.choice(image_files)
        welcome_message = Message([
            MessageSegment.at(user_id),
            MessageSegment.image(f"file:///{selected_image.absolute()}"),
            MessageSegment.text("\n欢迎新人！群高性能萝卜子ATRIだよ~\n发送“help”可获取帮助文档~")
        ])

    await welcome.finish(welcome_message)