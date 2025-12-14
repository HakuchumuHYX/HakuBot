import asyncio

from httpx import HTTPStatusError
from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.rule import Rule
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    MultiVar,
    Option,
    Query,
    Reply,
    on_alconna,
    store_true,
)
from nonebot_plugin_alconna.uniseg import Receipt
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from .._config import InfoManage, PixModel
from ..utils import MessageUtils
from .data_source import PixManage, config


def reply_check() -> Rule:
    """
    检查是否存在回复消息

    返回:
        Rule: Rule
    """

    async def _rule(bot: Bot, event: Event):
        if event.get_type() == "message":
            return bool(await reply_fetch(event, bot))
        return False

    return Rule(_rule)


_matcher = on_alconna(
    Alconna(
        "pix",
        Args["tags?", MultiVar(str)],
        Option("-n|--num", Args["num", int]),
        Option("-r|--r18", action=store_true, help_text="是否是r18"),
        Option("-noai", action=store_true, help_text="是否是过滤ai"),
        Option(
            "--nsfw",
            Args["nsfw_tag", MultiVar(int)],
            help_text="nsfw_tag，[0, 1, 2]",
        ),
        Option("--ratio", Args["ratio", str], help_text="图片比例，例如: 0.5,1.2"),
    ),
    aliases={"PIX"},
    priority=5,
    block=True,
)

_original_matcher = on_alconna(
    Alconna(["/"], "original"),
    priority=5,
    block=True,
    use_cmd_start=False,
    rule=reply_check(),
)


# 新增：处理单张图片（或组图中的某一页）下载和消息构建的辅助函数
async def process_single_image(pix: PixModel, page_index: int | None):
    # 确定当前要下载第几页
    current_p = page_index if page_index is not None else int(pix.img_p)

    # 调用 data_source.py 中修改过的 get_image，传入 page_index
    image = await PixManage.get_image(pix, is_original=False, page_index=page_index)

    if not image:
        return [f"获取图片 pid: {pix.pid} (P{current_p}) 失败..."], pix

    message_list = []
    if config.zxpix_show_info:
        # 构造信息字符串
        info_str = (
            f"title: {pix.title}\n"
            f"author: {pix.author}\n"
            f"pid: {pix.pid}"
        )
        # 如果是组图中的某一页，显示页码 Px
        if page_index is not None or pix.is_multiple:
            info_str += f" (P{current_p})"

        info_str += f"\nuid: {pix.uid}\n"

        message_list.append(info_str)

    message_list.append(image)
    return message_list, pix


@_matcher.handle()
async def _(
        bot: Bot,
        session: Uninfo,
        arparma: Arparma,
        tags: Query[tuple[str, ...]] = Query("tags", ()),
        num: Query[int] = Query("num", 1),
        nsfw: Query[tuple[int, ...]] = Query("nsfw_tag", ()),
        ratio: Query[str] = Query("ratio", ""),
):
    if num.result > 10:
        await MessageUtils.build_message("最多一次10张哦...").finish()
    allow_group_r18 = config.zxpix_allow_group_r18
    is_r18 = arparma.find("r18")
    if (
            not allow_group_r18
            and session.group
            and (is_r18 or 2 in nsfw.result)
            and session.user.id not in bot.config.superusers
    ):
        await MessageUtils.build_message("给我滚出克私聊啊变态！").finish()

    # 修改：强制默认为过滤 AI (is_ai = False)，无视用户是否输入 -noai
    # 原逻辑是 is_ai = False if arparma.find("noai") else None
    is_ai = False

    ratio_tuple = None
    ratio_tuple_split = []
    if "," in ratio.result:
        ratio_tuple_split = ratio.result.split(",")
    elif "，" in ratio.result:
        ratio_tuple_split = ratio.result.split("，")
    if ratio_tuple_split and len(ratio_tuple_split) < 2:
        return await MessageUtils.build_message("比例格式错误，请输入x,y").finish()
    if ratio_tuple_split:
        ratio_tuple = [float(ratio_tuple_split[0]), float(ratio_tuple_split[1])]
    if nsfw.result:
        for n in nsfw.result:
            if n not in [0, 1, 2]:
                return await MessageUtils.build_message(
                    "nsfw_tag格式错误，请输入0,1,2"
                ).finish()
    try:
        result = await PixManage.get_pix(
            tags.result,
            num.result,
            is_r18,
            is_ai,
            nsfw.result,
            ratio_tuple,
        )
        if not result.suc:
            await MessageUtils.build_message(result.info).send()
    except HTTPStatusError as e:
        logger.error(f"pix图库API出错... {type(e)}: {e}")
        await MessageUtils.build_message("pix图库API出错啦！").finish()
    if not result.data:
        await MessageUtils.build_message("没有找到相关tag/pix/uid的图片...").finish()

    # 修改：构建所有页面的下载任务
    download_tasks = []
    for pix in result.data:
        # 如果是多图，且总页数大于1，则遍历所有页码
        if pix.is_multiple and pix.page_count > 1:
            for p in range(pix.page_count):
                download_tasks.append(process_single_image(pix, p))
        else:
            # 否则只处理当前这一张
            download_tasks.append(process_single_image(pix, None))

    # 并发执行所有下载
    result_list = await asyncio.gather(*download_tasks)

    max_once_num2forward = config.zxpix_max_once_num2forward
    # 修改：判定合并转发时，使用扩展后的 result_list 长度
    if (
            max_once_num2forward
            and max_once_num2forward <= len(result_list)
            and session.group
    ):
        await MessageUtils.alc_forward_msg(
            [r[0] for r in result_list],
            session.user.id,
            next(iter(bot.config.nickname)),
        ).send()
    else:
        for r, pix in result_list:
            receipt: Receipt = await MessageUtils.build_message(r).send()
            msg_id = receipt.msg_ids[0]["message_id"]
            InfoManage.add(str(msg_id), pix)
    logger.info(f"pix调用 tags: {tags.result}")
    logger.info(f"pix调用 tags: {tags.result}")


@_original_matcher.handle()
async def _(bot: Bot, event: Event):
    reply: Reply | None = await reply_fetch(event, bot)
    if reply and (pix_model := InfoManage.get(str(reply.id))):
        try:
            result = await PixManage.get_image(pix_model, True)
            if not result:
                await MessageUtils.build_message("下载图片数据失败...").finish()
        except HTTPStatusError as e:
            logger.error(f"pix图库API出错... {type(e)}: {e}")
            await MessageUtils.build_message(
                f"pix图库API出错啦！ code: {e.response.status_code}"
            ).finish()
        receipt: Receipt = await MessageUtils.build_message(result).send(reply_to=True)
        msg_id = receipt.msg_ids[0]["message_id"]
        InfoManage.add(str(msg_id), pix_model)
    else:
        await MessageUtils.build_message(
            "没有找到该图片相关信息或数据已过期..."
        ).finish(reply_to=True)