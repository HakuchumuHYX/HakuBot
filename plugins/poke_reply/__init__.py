from nonebot import get_driver, logger
from nonebot.plugin import PluginMetadata

# 导入所有 Handlers 以注册事件响应器
from .handlers import poke
from .handlers import contribute
from .handlers import management
from .handlers import view
from .handlers import stats

# 导入文件监听器
from .file_monitor import file_monitor

__plugin_meta__ = PluginMetadata(
    name="戳一戳回复",
    description="在群聊中戳一戳机器人，随机回复已投稿的文本或图片",
    usage="""
    用法：
    1. 戳一戳机器人：随机回复一条已投稿的内容（文本或图片）
    2. 投稿 [文本/图片]：直接发送或回复消息进行投稿
    3. 申请删除：回复机器人发送的消息，申请删除该内容
    4. 查看投稿数：查看本群投稿统计
    5. 查看所有投稿：以合并转发形式查看本群所有投稿
    6. 启用/禁用文本转图片：(管理员) 开启后，长文本将转为图片发送
    """,
    type="application",
    homepage="https://github.com/HakuchumuHYX/HakuBot",
    supported_adapters={"~onebot.v11"},
)

driver = get_driver()

@driver.on_startup
async def startup():
    logger.info("正在启动 Poke Reply 插件...")
    if file_monitor.start_monitoring():
        logger.info("Poke Reply 文件监听器已启动")
    else:
        logger.error("Poke Reply 文件监听器启动失败")

@driver.on_shutdown
async def shutdown():
    logger.info("正在停止 Poke Reply 插件...")
    if file_monitor.stop_monitoring():
        logger.info("Poke Reply 文件监听器已停止")
