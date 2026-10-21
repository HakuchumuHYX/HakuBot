from nonebot.plugin import on_command
from nonebot import logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    MessageSegment,
)
from core.access import switch_rule
from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary


face_extractor = on_command(
    "save",
    aliases={"保存图片", "保存表情", "保存"},
    rule=command_boundary & switch_rule("sticker_saver"),
    priority=5,
    block=True,
)

TARGET_REDIRECT_URL = "https://pic.colanns.me"
TARGET_REDIRECT_URL_NT = "https://ntpic.colanns.me"


@face_extractor.handle()
async def handle_face_extraction(bot: Bot, event: MessageEvent):
    if event.reply:
        # 获取被回复的消息内容
        original_message = event.reply.message
        # 提取表情包并发送回去，静态表情包可以直接被保存
        for seg in original_message:
            logger.debug("seg: " + seg + " type: " + str(seg.type))
            if seg.type == "image":
                content = MessageSegment.text("表情：") + image_segment(
                    seg.data["url"], type_=0
                )
                # 用于 .gif 格式的表情包保存，加上一层跳转防止可能的检测
                url = (
                    str(seg.data["url"])
                    .replace("https://gchat.qpic.cn", TARGET_REDIRECT_URL)
                    .replace("https://multimedia.nt.qq.com.cn", TARGET_REDIRECT_URL_NT)
                )
                # async with AsyncClient() as client:
                #     # @deprecated 用于 .gif 格式的表情包保存，加上一层短链接防止可能的检测
                #     url = str(seg.data["url"]).replace("https://gchat.qpic.cn", TARGET_REDIRECT_URL)
                #     try:
                #         req = await client.get(url=url, timeout=3000)
                #         result = req.json()
                #         data = result.get("url")
                #     except Exception as e:
                #         data = "原始链接服务暂时不可用..."
                await bot.send(event, content + "原始链接：" + url)
                return
        await bot.send(event, "未在回复内容中检测到表情...")
    # 如果没有回复消息
    else:
        await bot.send(event, "只有回复表情才可以用捏")
