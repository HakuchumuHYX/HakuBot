from nonebot import logger
from utils.images.formats import to_rgb
from utils.logging import get_exc_desc
from utils.network.http import download_bytes
from utils.onebot.messages import find_forward_id, normalize_message_segments
import base64
import io
import re
from typing import Any, Literal
from PIL import Image, ImageOps
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from plugins.ai_assistant.config import plugin_config


ImagePurpose = Literal["chat", "generation"]

# chat 识图：超过此边长等比缩放并转 JPEG，减少视觉输入的体积
CHAT_IMAGE_MAX_SIZE = 1536
# 图生图参考图：只在超过此边长时缩放，保留 PNG/WebP/JPEG 格式和透明通道
REFERENCE_IMAGE_MAX_SIZE = 2048
FORWARD_MAX_NODES = 50
FORWARD_MAX_IMAGES = 8
FORWARD_MAX_TEXT_CHARS = 6000

_QUOTE_HEADER = "【引用的消息】"
_FORWARD_HEADER = "【引用的合并转发聊天记录】"
_QUESTION_HEADER = "【用户的问题】"

_SUPPORTED_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", "JPEG"),
    "PNG": ("image/png", "PNG"),
    "WEBP": ("image/webp", "WEBP"),
}


def _prepare_image_data(
    raw: bytes,
    *,
    max_size: int,
    preserve_format: bool,
) -> tuple[bytes, str]:
    """Validate and optionally resize an image, returning bytes and the real MIME type."""
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        source_format = (source.format or "").upper()
        image = ImageOps.exif_transpose(source)

        needs_resize = max_size > 0 and max(image.size) > max_size
        if (
            preserve_format
            and not needs_resize
            and source_format in _SUPPORTED_IMAGE_FORMATS
        ):
            mime_type, _ = _SUPPORTED_IMAGE_FORMATS[source_format]
            return raw, mime_type

        if needs_resize:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        if preserve_format and source_format in _SUPPORTED_IMAGE_FORMATS:
            output_format = source_format
        elif preserve_format:
            output_format = "PNG"
        else:
            output_format = "JPEG"

        if output_format == "JPEG":
            image = to_rgb(image)

        save_options: dict[str, Any] = {}
        if output_format == "JPEG":
            save_options = {"quality": 85, "optimize": True}
        elif output_format == "WEBP":
            save_options = {"quality": 90}

        buffer = io.BytesIO()
        image.save(buffer, format=output_format, **save_options)
        mime_type, _ = _SUPPORTED_IMAGE_FORMATS[output_format]
        return buffer.getvalue(), mime_type


async def download_image_as_data_url(
    url: str,
    *,
    purpose: ImagePurpose = "chat",
) -> str:
    """Download and normalize an image for chat vision or Images API editing."""
    data = await download_bytes(url, proxy=plugin_config.proxy, timeout=20)
    if purpose == "chat":
        return image_data_url(data, max_size=CHAT_IMAGE_MAX_SIZE, preserve_format=False)
    return image_data_url(data, max_size=REFERENCE_IMAGE_MAX_SIZE, preserve_format=True)


def image_data_url(raw: bytes, *, max_size: int, preserve_format: bool) -> str:
    data, mime_type = _prepare_image_data(
        raw, max_size=max_size, preserve_format=preserve_format
    )
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _format_forward_sender(node: dict[str, Any]) -> str:
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


def _text_from_segment(segment: dict[str, Any], image_index: int | None = None) -> str:
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


async def parse_forward_message_content(bot: Any, forward_id: str) -> list[dict]:
    """拉取并格式化合并转发内容，返回 OpenAI 多模态 content 列表。"""
    forward_data = await bot.get_forward_msg(id=forward_id)
    nodes = forward_data.get("messages", []) if isinstance(forward_data, dict) else []

    max_nodes = FORWARD_MAX_NODES
    max_images = FORWARD_MAX_IMAGES
    max_text_chars = FORWARD_MAX_TEXT_CHARS

    lines = [_FORWARD_HEADER]
    image_parts: list[dict] = []
    image_count = 0

    for index, raw_node in enumerate(nodes[:max_nodes], start=1):
        if not isinstance(raw_node, dict):
            continue

        sender = _format_forward_sender(raw_node)
        segments = normalize_message_segments(raw_node.get("message", ""))

        pieces: list[str] = []
        for segment in segments:
            seg_type = segment.get("type")
            image_index = None
            if seg_type == "image":
                image_count += 1
                image_index = image_count
                data = segment.get("data") or {}
                image_url = data.get("url")
                if image_url and len(image_parts) < max_images:
                    try:
                        b64_img = await download_image_as_data_url(image_url)
                        image_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": b64_img},
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"转发消息中的图片下载失败，已跳过: {get_exc_desc(e)}"
                        )

            text = _text_from_segment(segment, image_index=image_index)
            if text:
                pieces.append(text)

        node_text = " ".join(pieces).strip() or "[空消息]"
        lines.append(f"{index}. {sender}: {node_text}")

        current_text = "\n".join(lines)
        if len(current_text) >= max_text_chars:
            lines[-1] = lines[-1][
                : max(0, len(lines[-1]) - (len(current_text) - max_text_chars))
            ]
            lines.append("...[合并转发内容过长，已截断]")
            break

    if len(nodes) > max_nodes:
        lines.append(f"...[仅展开前 {max_nodes} 条合并转发节点]")
    if image_count > len(image_parts):
        lines.append(
            f"...[合并转发内共有 {image_count} 张图片，已传入 {len(image_parts)} 张]"
        )

    text_part = {"type": "text", "text": "\n".join(lines).strip()}
    return [text_part] + image_parts


async def _segments_to_content(
    message: Message, image_purpose: ImagePurpose
) -> tuple[str, list[dict]]:
    texts: list[str] = []
    images: list[dict] = []
    for seg in message:
        if seg.is_text():
            text = seg.data.get("text", "").strip()
            if text:
                texts.append(text)
        elif seg.type == "image" and seg.data.get("url"):
            b64_img = await download_image_as_data_url(
                seg.data["url"], purpose=image_purpose
            )
            images.append({"type": "image_url", "image_url": {"url": b64_img}})
    return "\n".join(texts), images


async def parse_message_content(
    event: MessageEvent,
    args: Message,
    *,
    bot: Bot | None = None,
    quote: bool = True,
    image_purpose: ImagePurpose = "chat",
) -> list:
    """
    解析消息内容，构建OpenAI格式的 messages content 列表。
    quote=True 时带上被回复的消息；传了 bot 才展开被回复的合并转发。
    有引用时给各段加标注，让模型分得清哪段是引用、哪段是提问。
    """
    content_list = []

    if quote and event.reply:
        reply_msg = event.reply.message
        forward_id = find_forward_id(reply_msg) if bot else None
        if forward_id:
            content_list.extend(await parse_forward_message_content(bot, forward_id))
        else:
            text, images = await _segments_to_content(reply_msg, image_purpose)
            if text or images:
                label = f"{_QUOTE_HEADER}\n{text}" if text else _QUOTE_HEADER
                content_list.append({"type": "text", "text": label})
                content_list.extend(images)

    text, images = await _segments_to_content(args, image_purpose)
    if text and content_list:
        text = f"{_QUESTION_HEADER}\n{text}"
    if text:
        content_list.append({"type": "text", "text": text})
    content_list.extend(images)

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
    text = re.sub(r"\*\*|__|\*|_", "", text)

    # 2. 移除标题标记 (# Title)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

    # 3. 移除链接格式 ([text](url))
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)

    # 4. 移除图片格式 (![alt](url))
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)

    # 5. 移除代码块反引号 (```, `)
    text = re.sub(r"`", "", text)

    # 6. 移除引用符号 (>)
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    # 7. 去除多余的首尾空白
    return text.strip()


def parse_data_url(data_url: str) -> tuple[str, str]:
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
