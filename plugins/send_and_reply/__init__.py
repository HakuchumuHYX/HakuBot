from nonebot import get_driver
from nonebot.plugin import PluginMetadata

# 导入功能模块
from . import send
from . import reply

__plugin_meta__ = PluginMetadata(
    name="消息转发与回复插件",
    description="用户向超级用户发送消息，超级用户可回复",
    usage=(
        "用户发送消息: @机器人 send <消息内容>\n"
        "超级用户回复: !reply <用户ID> <回复内容> 或 直接回复转发的消息"
    ),
    type="application",
    supported_adapters={"~onebot.v11"},
    extra={
        "author": "Your Name",
        "version": "1.0.0"
    }
)