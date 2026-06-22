import base64
import io
import httpx
import re
from typing import Any, Dict, List, Optional, Tuple
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


def find_forward_id(message: Message) -> Optional[str]:
    """从消息段中提取合并转发 id。"""
    for segment in message:
        if segment.type == "forward":
            forward_id = segment.data.get("id")
            if forward_id:
                return str(forward_id)
    return None


def _segment_to_dict(segment: Any) -> Optional[Dict[str, Any]]:
    if isinstance(segment, MessageSegment):
        return {"type": segment.type, "data": dict(segment.data)}
    if isinstance(segment, dict):
        if "type" in segment:
            return segment
    if isinstance(segment, str) and segment:
        return {"type": "text", "data": {"text": segment}}
    return None


def normalize_message_segments(content: Any) -> List[Dict[str, Any]]:
    """兼容 OneBot 返回的 str/dict/list/Message/MessageSegment 消息内容。"""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "data": {"text": content}}] if content else []
    if isinstance(content, MessageSegment):
        segment = _segment_to_dict(content)
        return [segment] if segment else []
    if isinstance(content, Message):
        return [seg for item in content if (seg := _segment_to_dict(item))]
    if isinstance(content, dict):
        segment = _segment_to_dict(content)
        return [segment] if segment else []
    if isinstance(content, list):
        segments: List[Dict[str, Any]] = []
        for item in content:
            segments.extend(normalize_message_segments(item))
        return segments
    return []


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _format_forward_sender(node: Dict[str, Any]) -> str:
    sender = node.get("sender") or {}
    user_id = (
        _get_value(sender, "user_id")
        or node.get("user_id")
        or node.get("uin")
        or node.get("qq")
    )
    name = (
        _get_value(sender, "card")
        or _get_value(sender, "nickname")
        or _get_value(sender, "name")
        or node.get("card")
        or node.get("nickname")
        or node.get("name")
    )

    if name and user_id:
        return f"{name}({user_id})"
    if name:
        return str(name)
    if user_id:
        return str(user_id)
    return "未知用户"


def _text_from_segment(segment: Dict[str, Any], image_index: Optional[int] = None) -> str:
    seg_type = segment.get("type")
    data = segment.get("data") or {}

    if seg_type == "text":
        return str(data.get("text") or "").strip()
    if seg_type == "image":
        return f"[图片{image_index}]" if image_index else "[图片]"
    if seg_type == "at":
        qq = data.get("qq") or data.get("user_id")
        return f"[@{qq}]" if qq else "[@]"
    if seg_type == "face":
        face_id = data.get("id")
        return f"[表情:{face_id}]" if face_id else "[表情]"
    if seg_type == "forward":
        return "[合并转发]"
    if seg_type:
        return f"[{seg_type}]"
    return ""


async def parse_forward_message_content(bot: Any, forward_id: str) -> List[dict]:
    """拉取并格式化合并转发内容，返回 OpenAI 多模态 content 列表。"""
    forward_data = await bot.get_forward_msg(id=forward_id)
    nodes = forward_data.get("messages", []) if isinstance(forward_data, dict) else []

    max_nodes = max(1, int(getattr(plugin_config.chat, "forward_max_nodes", 50) or 50))
    max_images = max(0, int(getattr(plugin_config.chat, "forward_max_images", 8) or 0))
    max_text_chars = max(500, int(getattr(plugin_config.chat, "forward_max_text_chars", 6000) or 6000))
    include_images = bool(getattr(plugin_config.chat, "forward_include_images", True))

    lines = ["【用户回复的合并转发聊天记录】"]
    image_parts: List[dict] = []
    image_count = 0

    for index, raw_node in enumerate(nodes[:max_nodes], start=1):
        if not isinstance(raw_node, dict):
            continue

        sender = _format_forward_sender(raw_node)
        segments = normalize_message_segments(raw_node.get("message", ""))

        pieces: List[str] = []
        for segment in segments:
            seg_type = segment.get("type")
            image_index = None
            if seg_type == "image":
                image_count += 1
                image_index = image_count
                data = segment.get("data") or {}
                image_url = data.get("url")
                if include_images and image_url and len(image_parts) < max_images:
                    try:
                        b64_img = await download_image_as_base64(image_url)
                        image_parts.append({
                            "type": "image_url",
                            "image_url": {"url": b64_img},
                        })
                    except Exception:
                        pass

            text = _text_from_segment(segment, image_index=image_index)
            if text:
                pieces.append(text)

        node_text = " ".join(pieces).strip() or "[空消息]"
        lines.append(f"{index}. {sender}: {node_text}")

        current_text = "\n".join(lines)
        if len(current_text) >= max_text_chars:
            lines[-1] = lines[-1][: max(0, len(lines[-1]) - (len(current_text) - max_text_chars))]
            lines.append("...[合并转发内容过长，已截断]")
            break

    if len(nodes) > max_nodes:
        lines.append(f"...[仅展开前 {max_nodes} 条合并转发节点]")
    if image_count > len(image_parts):
        lines.append(f"...[合并转发内共有 {image_count} 张图片，已传入 {len(image_parts)} 张]")

    text_part = {"type": "text", "text": "\n".join(lines).strip()}
    return [text_part] + image_parts


async def parse_message_content(*params: Any, include_forward: bool = False) -> list:
    """
    解析消息内容，构建OpenAI格式的 messages content 列表。
    支持：命令后的参数、回复的消息（含图片）、可选解析回复的合并转发。
    """
    if len(params) == 2:
        bot = None
        event, args = params
    elif len(params) == 3:
        bot, event, args = params
    else:
        raise TypeError("parse_message_content expects (event, args) or (bot, event, args)")

    content_list = []

    # 1. 处理回复的消息 (Reply)
    if event.reply:
        reply_msg = event.reply.message
        handled_forward = False
        if include_forward and bot:
            forward_id = find_forward_id(reply_msg)
            if forward_id:
                content_list.extend(await parse_forward_message_content(bot, forward_id))
                handled_forward = True

        if not handled_forward:
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
