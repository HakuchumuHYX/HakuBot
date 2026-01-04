from typing import Tuple, Optional
from nonebot import on_regex
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import RegexGroup
from nonebot.log import logger
from nonebot.exception import FinishedException

from .config import plugin_config
from .data_manager import get_binding
from .render import render_profile

profile_matcher = on_regex(
    r"^(cn|jp|en|tw|kr)?(个人信息|pjskprofile)$",
    priority=10,
    block=True
)


def construct_url(server: str, pjsk_id: str) -> str:
    base_url = plugin_config.url.rstrip("/")
    token = plugin_config.token
    if not base_url or not token:
        logger.error("配置文件中的 url 或 token 为空！")
        return ""
    full_url = f"https://{base_url}/{server}/{pjsk_id}?token={token}"
    return full_url


@profile_matcher.handle()
async def _(event: MessageEvent, groups: Tuple[Optional[str], str] = RegexGroup()):
    user_id = event.get_user_id()
    server_prefix = groups[0]

    if server_prefix:
        server = server_prefix.lower()
    else:
        server = "jp"

    pjsk_id = get_binding(user_id, server)

    if not pjsk_id:
        server_name_map = {"jp": "日服", "cn": "国服", "en": "国际服", "tw": "台服", "kr": "韩服"}
        display_server = server_name_map.get(server, server)
        await profile_matcher.finish(f"❌ 你还没有绑定{display_server}的ID。\n请使用“{server}绑定+ID”进行绑定。")
        return

    target_url = construct_url(server, pjsk_id)
    if not target_url:
        await profile_matcher.finish("❌ 插件配置缺失(url或token)。")
    await profile_matcher.send("正在获取个人信息，请稍候...")

    try:
        image_bytes = await render_profile(target_url)
        await profile_matcher.finish(MessageSegment.image(image_bytes))

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"PJSK Profile 处理异常: {e}")
        await profile_matcher.finish(f"获取SnowyBot版个人资料页出错，请稍后再试")
