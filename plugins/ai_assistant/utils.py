import base64
import httpx
import re
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from .config import plugin_config


async def download_image_as_base64(url: str) -> str:
    """下载图片并转换为base64字符串（data:...;base64,...），用于 OpenAI 多模态输入。"""
    # 设置20秒超时，防止下载大图时阻塞过久
    async with httpx.AsyncClient(proxy=plugin_config.proxy, timeout=20.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        b64 = base64.b64encode(resp.content).decode("utf-8")
        mime = "image/jpeg"
        return f"data:{mime};base64,{b64}"


async def download_image_as_onebot_base64(url: str) -> str:
    """
    下载图片并转换为 OneBot v11 可发送的 base64 格式：base64://<纯base64>

    用途：当 OneBot 端无法直接下载远端图片 URL（鉴权/签名/防盗链等）时，
    由机器人侧先下载到本地内存，再以 base64 方式发图，避免 OneBot 端拉取失败。
    """
    async with httpx.AsyncClient(proxy=plugin_config.proxy, timeout=30.0) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()

    b64 = base64.b64encode(resp.content).decode("utf-8")
    return f"base64://{b64}"


async def parse_message_content(event, args: Message) -> list:
    """
    解析消息内容，构建OpenAI格式的 messages content 列表。
    支持：命令后的参数、回复的消息（含图片）。
    """
    content_list = []

    # 1. 处理回复的消息 (Reply)
    if event.reply:
        reply_msg = event.reply.message
        for seg in reply_msg:
            if seg.is_text():
                text = seg.data.get("text", "").strip()
                if text:
                    content_list.append({"type": "text", "text": text})
            elif seg.type == "image":
                url = seg.data.get("url")
                if url:
                    b64_img = await download_image_as_base64(url)
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": b64_img}
                    })

    # 2. 处理当前消息的参数 (Args)
    for seg in args:
        if seg.is_text():
            text = seg.data.get("text", "").strip()
            if text:
                content_list.append({"type": "text", "text": text})
        elif seg.type == "image":
            url = seg.data.get("url")
            if url:
                b64_img = await download_image_as_base64(url)
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": b64_img}
                })

    return content_list


def extract_pure_text(content_list: list) -> str:
    """从解析后的内容中仅提取文本，用于生图Prompt"""
    texts = [item["text"] for item in content_list if item["type"] == "text"]
    return " ".join(texts)


def remove_markdown(text: str) -> str:
    """
    移除文本中的 Markdown 格式符号，使其更适合聊天窗口显示
    """
    if not text:
        return ""

    # 1. 移除加粗/斜体 (**text**, *text*, __text__, _text_)
    text = re.sub(r'\*\*|__|\*|_', '', text)

    # 2. 移除标题标记 (# Title)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    # 3. 移除链接格式 ([text](url))
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)

    # 4. 移除图片格式 (![alt](url))
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)

    # 5. 移除代码块反引号 (```, `)
    text = re.sub(r'`', '', text)

    # 6. 移除引用符号 (>)
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # 7. 去除多余的首尾空白
    return text.strip()
