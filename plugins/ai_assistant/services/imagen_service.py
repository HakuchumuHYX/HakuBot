import base64
from typing import Optional

from nonebot.log import logger

from plugins.utils.llm import (
    LLMClientConfig,
    image_edit as sdk_image_edit,
    image_generation as sdk_image_generation,
)

from ..config import plugin_config
from ..utils import parse_data_url


_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _image_llm_config() -> LLMClientConfig:
    rc = plugin_config.resolve("image")
    timeout = plugin_config.image.timeout
    if timeout is None:
        timeout = plugin_config.timeout
    return LLMClientConfig(
        provider=rc.provider,
        api_key=rc.api_key,
        base_url=rc.base_url,
        model=plugin_config.image.model,
        timeout=timeout,
        proxy=plugin_config.proxy,
        max_retries=plugin_config.image.api_max_retries,
    )


def _build_image_request(
    content_list: list[dict],
    extra_context: Optional[str],
) -> tuple[str, list[tuple[str, bytes, str]]]:
    prompt_parts: list[str] = []
    images: list[tuple[str, bytes, str]] = []

    for item in content_list:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "text":
            text = (item.get("text") or "").strip()
            if text:
                prompt_parts.append(text)
            continue

        if item_type != "image_url":
            continue

        url = ((item.get("image_url") or {}).get("url") or "").strip()
        if not url.startswith("data:"):
            logger.warning("忽略非 data URL 的生图参考图")
            continue

        mime, encoded = parse_data_url(url)
        extension = _IMAGE_EXTENSIONS.get(mime.lower())
        if not extension:
            raise ValueError(f"不支持的参考图格式: {mime}")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ValueError("参考图包含无效的 base64 数据。") from exc
        images.append((f"ref{len(images) + 1}.{extension}", image_bytes, mime))

    if not prompt_parts and images:
        prompt_parts.append("Use the provided image as a visual reference.")

    user_prompt = "\n".join(prompt_parts).strip()
    if not user_prompt:
        raise ValueError("生图请求缺少有效的文字描述或参考图。")

    prompt_prefix = plugin_config.image.prompt_prefix.strip()
    prompt = user_prompt
    if prompt_prefix:
        prompt = f"{prompt_prefix}\n\n【用户需求】\n{user_prompt}"

    if extra_context:
        prompt += (
            "\n\n[Web Search Context - Reference Only]\n"
            "以下内容仅用于补充事实和外观设定。提炼其中对画面有用的信息，"
            "若与用户描述冲突，以用户描述为准。\n\n"
            + extra_context.strip()
        )

    return prompt, images


async def call_image_generation(
    content_list: list[dict],
    extra_context: Optional[str] = None,
) -> str:
    """根据是否包含参考图调用生图或图片编辑接口。"""
    prompt, images = _build_image_request(content_list, extra_context)
    config = _image_llm_config()

    if images:
        logger.debug("正在请求图生图 (images/edits)")
        result = await sdk_image_edit(
            config,
            prompt=prompt,
            images=images,
            model=plugin_config.image.model,
            size=plugin_config.image.size,
            quality=plugin_config.image.quality,
        )
    else:
        logger.debug("正在请求文生图 (images/generations)")
        result = await sdk_image_generation(
            config,
            prompt=prompt,
            model=plugin_config.image.model,
            size=plugin_config.image.size,
            quality=plugin_config.image.quality,
        )

    logger.info("成功提取生图结果")
    return result.image_url
