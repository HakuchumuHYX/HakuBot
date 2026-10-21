from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import (
    Message,
    Bot,
    MessageEvent,
    GroupMessageEvent,
)
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import Matcher

from plugins.lunabot_imgexp.core import search_image
from utils.onebot.forward import send_forward_msg
from utils.onebot.media import image_segment
from plugins.lunabot_imgexp import twitter  # noqa: F401

from core.access import is_feature_enabled
from core.cooldown import check_cd, update_cd

PLUGIN_NAME = "lunabot_imgexp"

imgexp = on_command(
    "搜图", aliases={"以图搜图", "imgexp", "search"}, priority=5, block=True
)


@imgexp.handle()
async def _(
    bot: Bot,
    event: MessageEvent,
    state: T_State,
    matcher: Matcher,
    arg: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 检查功能开关
        if not is_feature_enabled(PLUGIN_NAME, "search", group_id, user_id):
            await imgexp.finish()

        # 2. 检查功能 CD (key: lunabot_imgexp:search)
        cd_key = f"{PLUGIN_NAME}:search"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await imgexp.finish(
                f"搜图功能冷却中，请等待 {cd_remain} 秒", at_sender=True
            )

    # 尝试从参数中提取图片
    img_urls = extract_image_urls(arg)
    if img_urls:
        state["img_urls"] = img_urls

    # 如果参数中没有图片，检查是否是回复消息
    if not state.get("img_urls") and event.reply:
        img_urls = extract_image_urls(event.reply.message)
        if img_urls:
            state["img_urls"] = img_urls


@imgexp.got("img_urls", prompt="请发送图片")
async def _(bot: Bot, event: MessageEvent, state: T_State):
    img_urls = state["img_urls"]
    # 如果在 got 阶段用户发送的是包含图片的 Message，这里 img_urls 可能是 Message 对象，需要再次提取
    if isinstance(img_urls, Message):
        img_urls = extract_image_urls(img_urls)

    if not img_urls:
        await imgexp.finish("没有检测到图片，请重新发送命令。")

    await imgexp.send("正在搜索图片，请稍候...")

    cd_updated = False  # 成功发送结果后才扣 CD，多张图只扣一次
    for url in img_urls:
        try:
            # search_image 内部会先下载图片并限制大小
            res_img, results = await search_image(url)

            # 发送结果图片
            # 构建合并转发消息列表
            forward_messages = [image_segment(res_img)]

            # 发送文字结果详情
            # 将每一条搜索结果拆分成独立消息（合并转发的独立 node），方便移动端逐条复制链接
            for result in results:
                # 错误信息也单独成条
                if result.error:
                    forward_messages.append(
                        f"来自 {result.source} 的结果:\n{result.error}".strip()
                    )
                    continue

                # 没有结果则跳过（不发送“未找到结果”文字，避免刷屏）
                if not result.results:
                    continue

                # 每个 item 独立成条
                for i, item in enumerate(result.results):
                    title_str = f"[{item.title}]\n" if item.title else ""
                    sim_str = (
                        f"({item.similarity:.1f}%)\n"
                        if item.similarity is not None
                        else ""
                    )
                    # GoogleLens 的结果可能带来源站点（item.source）
                    from_str = (
                        f"From {item.source}\n" if getattr(item, "source", None) else ""
                    )
                    forward_messages.append(
                        f"来自 {result.source} 的结果 #{i + 1}\n{from_str}{title_str}{sim_str}{item.url}".strip()
                    )

            await send_forward_msg(bot, event, items=forward_messages)

            # 发送成功后再更新 CD（仅群聊，多张图只扣一次）
            if not cd_updated and isinstance(event, GroupMessageEvent):
                update_cd(
                    f"{PLUGIN_NAME}:search", str(event.group_id), str(event.user_id)
                )
                cd_updated = True

        except Exception as e:
            logger.exception("搜图失败")
            await imgexp.send(f"搜图失败: {e}")
