from nonebot import on_notice, require
from nonebot.adapters.onebot.v11 import GroupIncreaseNoticeEvent, Message, MessageSegment
import random
from pathlib import Path
from ..plugin_manager.enable import is_plugin_enabled

require("nonebot_plugin_localstore")
import nonebot_plugin_localstore as store

# === 配置区域 ===
# 触发特殊图片的概率 (0.0 - 1.0)
SPECIAL_PROBABILITY = 0.4
# 支持的图片后缀
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

# === 目录初始化 ===
welcome_base_dir = store.get_plugin_data_dir()
normal_dir = welcome_base_dir / "normal"
special_dir = welcome_base_dir / "special"

welcome = on_notice()


@welcome.handle()
async def handle_welcome(event: GroupIncreaseNoticeEvent):
    if event.notice_type != "group_increase":
        return

    group_id = str(event.group_id)
    if not is_plugin_enabled("welcome", group_id, "0"):
        return

    user_id = event.user_id

    # 1. 确保目录结构存在
    normal_dir.mkdir(parents=True, exist_ok=True)
    special_dir.mkdir(parents=True, exist_ok=True)

    # 2. 图片选择逻辑
    selected_image = None

    # 判定是否触发特殊图片
    is_lucky = random.random() < SPECIAL_PROBABILITY

    if is_lucky:
        # 尝试从 special 目录获取图片
        special_files = [
            f for f in special_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if special_files:
            selected_image = random.choice(special_files)

    # 如果没有选中图片（没命中概率，或者命中但special文件夹是空的），则从 normal 获取
    if selected_image is None:
        normal_files = [
            f for f in normal_dir.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
        if normal_files:
            selected_image = random.choice(normal_files)

    # 3. 构建并发送消息
    msg_segments = [MessageSegment.at(user_id)]

    if selected_image:
        # 如果选到了图片
        msg_segments.append(MessageSegment.image(f"file:///{selected_image.absolute()}"))
        msg_segments.append(MessageSegment.text("\n欢迎新人！群高性能萝卜子ATRIだよ~\n发送“help”可获取帮助文档~"))
    else:
        # 如果 normal 和 special 都没有图片，发送纯文字
        msg_segments.append(MessageSegment.text("\n欢迎新人！群高性能萝卜子ATRIだよ~\n发送“help”可获取帮助文档~"))

    # 统一 finish，避免重复发送
    await welcome.finish(Message(msg_segments))
