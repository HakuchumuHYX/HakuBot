import base64
import io
import httpx
import re
from typing import Optional, Tuple
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image
from .config import plugin_config


async def download_image_as_base64(url: str, max_size: Optional[int] = None) -> str:
    """下载图片并转换为base64字符串（data:...;base64,...），用于 OpenAI 多模态输入。

    Args:
        url: 图片 URL
        max_size: 图片最大边长（像素）。超过则等比缩放并 JPEG 压缩。
                  为 None 时从 config.chat.image_max_size 读取，为 0 则不压缩。
    """
    if max_size is None:
        max_size = getattr(plugin_config.chat, "image_max_size", 1536)

    async with httpx.AsyncClient(proxy=plugin_config.proxy, timeout=20.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.content

    # 如果配置了 max_size 且 > 0，使用 Pillow 缩放 + JPEG 压缩
    if max_size and max_size > 0:
        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            if max(w, h) > max_size:
                ratio = max_size / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
            # 统一转 RGB（去掉 alpha 通道）再压缩为 JPEG
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            raw = buf.getvalue()
        except Exception:
            pass  # Pillow 处理失败时回退使用原始数据

    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


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


def parse_data_url(data_url: str) -> Tuple[str, str]:
    """
    Parse `data:<mime>;base64,<data>` to (mime, base64_data)
    """
    if not data_url:
        raise ValueError("Empty data url")
    if not data_url.startswith("data:"):
        raise ValueError("Not a data url")
    # data:image/jpeg;base64,AAAA...
    header, b64 = data_url.split(",", 1)
    header = header[5:]  # remove 'data:'
    mime = header.split(";", 1)[0].strip() if ";" in header else header.strip()
    if not mime:
        mime = "application/octet-stream"
    return mime, b64.strip()
