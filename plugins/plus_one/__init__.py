from nonebot.plugin import PluginMetadata, inherit_supported_adapters
from nonebot import require

require("nonebot_plugin_session")

from plugins.plus_one.config import Config
from plugins.plus_one.handler import plus  # noqa: F401
from plugins.plus_one import command_handler  # noqa: F401


__plugin_meta__ = PluginMetadata(
    name="复读姬+1 PlusOne",
    description="全新复读姬，支持文本、图片、表情甚至是转发分享卡片复读，任意群聊触发 +1，姬就 +1。\n"
    "轻巧、专注，不使用任何数据库，不使用任何文件存储\n",
    usage="复读姬，任意群聊触发 +1，姬就 +1",
    config=Config,
    homepage="https://github.com/yejue/nonebot-plugin-plus-one",
    type="application",
    supported_adapters=inherit_supported_adapters("nonebot_plugin_session"),
)
