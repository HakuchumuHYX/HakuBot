from typing import List, Union
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Message, MessageSegment, Bot, Event, MessageEvent, GroupMessageEvent
from nonebot.adapters.onebot.v11.helpers import extract_image_urls
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.rule import to_me

from .core import search_image
from .config import config
from ..utils.tools import get_logger, send_forward_msg, TempFilePath
from ..utils.network import download_image
from ..utils.image_utils import path_to_base64_image
from . import twitter  # noqa: F401

try:
    from ..plugin_manager.enable import is_plugin_enabled, is_feature_enabled
    from ..plugin_manager.cd_manager import check_cd, update_cd
    MANAGER_AVAILABLE = True
except ImportError:
    logger = get_logger("ImgExp") # logger 在 try 块前定义可能更好，但这里为了保持 diff 简洁
    logger.warning("未找到 plugin_manager 插件，将跳过管理功能检查。")
    MANAGER_AVAILABLE = False

PLUGIN_NAME = "lunabot_imgexp"
logger = get_logger("ImgExp")

imgexp = on_command("搜图", aliases={"以图搜图", "imgexp", "search"}, priority=5, block=True)

@imgexp.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State, matcher: Matcher, arg: Message = CommandArg()):
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 检查插件总开关
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await imgexp.finish()

        # 2. 检查功能开关
        if not is_feature_enabled(PLUGIN_NAME, "search", group_id, user_id):
            await imgexp.finish()

        # 3. 检查功能 CD (key: lunabot_imgexp:search)
        cd_key = f"{PLUGIN_NAME}:search"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await imgexp.finish(f"搜图功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        # 4. 更新 CD
        update_cd(cd_key, group_id, user_id)

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
    
    for url in img_urls:
        try:
            # 获取图片大小，这里简单用 Content-Length 头判断可能不准，或者直接下载后判断
            # search_image 内部会下载并处理，这里主要做个大致检查或直接传 URL
            
            # 为了获取 img_size，我们可以先 HEAD 请求一下，或者直接传 0 让 core 下载
            # core.py 中 search_image 的 img_size 主要是为了限制过大图片，但如果直接传 URL 给 SauceNAO/GoogleLens，
            # 它们通常自己处理。这里我们假设不预先检查大小，或者在 core.py 完善下载逻辑。
            # 目前 core.py 的 search_image 接受 img_url 和 img_size
            
            # 搜索图片
            res_img, results = await search_image(url)
            
            # 发送结果图片
            # 使用临时文件保存图片，避免 Base64 过大导致 WebSocket 超时
            with TempFilePath(ext="png") as tmp_path:
                res_img.save(tmp_path, format='PNG')
                
                # 构建合并转发消息列表
                forward_messages = [path_to_base64_image(tmp_path)]

                # 发送文字结果详情
                # 将每一条搜索结果拆分成独立消息（合并转发的独立 node），方便移动端逐条复制链接
                for result in results:
                    # 错误信息也单独成条
                    if result.error:
                        forward_messages.append(f"来自 {result.source} 的结果:\n{result.error}".strip())
                        continue

                    # 没有结果则跳过（不发送“未找到结果”文字，避免刷屏）
                    if not result.results:
                        continue

                    # 每个 item 独立成条
                    for i, item in enumerate(result.results):
                        title_str = f"[{item.title}]\n" if item.title else ""
                        sim_str = f"({item.similarity:.1f}%)\n" if item.similarity is not None else ""
                        # GoogleLens 的结果可能带来源站点（item.source）
                        from_str = f"From {item.source}\n" if getattr(item, "source", None) else ""
                        forward_messages.append(
                            f"来自 {result.source} 的结果 #{i + 1}\n{from_str}{title_str}{sim_str}{item.url}".strip()
                        )

                await send_forward_msg(bot, event, forward_messages)
            
        except Exception as e:
            logger.error(f"搜图失败: {e}")
            import traceback
            traceback.print_exc()
            await imgexp.send(f"搜图失败: {e}")
