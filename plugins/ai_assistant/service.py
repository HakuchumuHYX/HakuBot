import httpx
import json
import re
from typing import Tuple, Optional, List
from nonebot.log import logger
from .config import plugin_config

HEADERS = {
    "Authorization": f"Bearer {plugin_config.api_key}",
    "Content-Type": "application/json"
}


async def call_chat_completion(messages: list) -> Tuple[str, str, int]:
    """
    调用聊天接口
    """
    payload = {
        "model": plugin_config.chat_model,
        "messages": messages,
        "max_tokens": 1000
    }

    async with httpx.AsyncClient(
            base_url=plugin_config.base_url,
            proxy=plugin_config.proxy,
            timeout=plugin_config.timeout
    ) as client:
        resp = await client.post("/chat/completions", json=payload, headers=HEADERS)

        if resp.status_code != 200:
            raise Exception(f"API Error {resp.status_code}: {resp.text}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        total_tokens = data.get("usage", {}).get("total_tokens", 0)

        return content, plugin_config.chat_model, total_tokens


async def call_image_generation(content_list: List[dict]) -> str:
    """
    调用生图接口 (支持多模态输入 + 适配 Chat 协议)
    """
    system_instruction = (
        "Please generate an image based on the user's request. "
        "Return ONLY the image URL in Markdown format, like this: ![image](https://...). "
        "Ensure the response is a valid Markdown image link and nothing else. "
        "生成的图片画风用漫画/二次元画风为佳。"
    )

    payload = {
        "model": plugin_config.image_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": content_list}
        ]
    }

    logger.debug(f"正在请求生图 (Chat协议): {plugin_config.base_url}")
    debug_payload = payload.copy()
    debug_payload["messages"] = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": "[(Content with Image Base64 data hidden)]"}
    ]
    logger.debug(f"Payload 概览: {json.dumps(debug_payload, ensure_ascii=False)}")

    try:
        async with httpx.AsyncClient(
                base_url=plugin_config.base_url,
                proxy=plugin_config.proxy,
                timeout=plugin_config.timeout
        ) as client:
            resp = await client.post("/chat/completions", json=payload, headers=HEADERS)

            if resp.status_code != 200:
                logger.error(f"API 返回: {resp.text}")
                raise Exception(f"API Error {resp.status_code}")

            data = resp.json()
    except httpx.TimeoutException:
        raise Exception(f"请求超时，已等待 {plugin_config.timeout} 秒。")

    try:
        choice = data["choices"][0]
        message = choice.get("message", {})
        content = message.get("content")

        if "images" in message and isinstance(message["images"], list) and message["images"]:
            image_obj = message["images"][0]
            if "image_url" in image_obj and "url" in image_obj["image_url"]:
                logger.info("成功从 message.images 字段提取到图片URL")
                return image_obj["image_url"]["url"]

        if content:
            # 优化正则: 支持跨行匹配 (re.DOTALL)，允许 ] 和 ( 之间有空格
            match = re.search(r'!\[.*?\]\s*\((.*?)\)', content, re.DOTALL)
            if match:
                return match.group(1).strip()

            urls = re.findall(r'(https?://[^\s)"]+)', content)
            for url in urls:
                if not url.endswith(('.py', '.html', '.css', '.js')):
                    return url

        if content is None:
            finish_reason = choice.get("finish_reason")
            if finish_reason == "content_filter":
                raise Exception("生图请求被AI模型的安全策略拒绝了(Content Filter)。")

            logger.error(f"API返回数据异常。完整数据: {json.dumps(data, ensure_ascii=False)}")
            raise Exception("API 返回成功，但未找到图片地址。")

        preview = content[:50].replace('\n', ' ')
        logger.warning(f"生图失败，完整模型回复: {content}")
        raise Exception(f"模型回复了文本但未包含图片链接: “{preview}...”")

    except (KeyError, IndexError) as e:
        logger.error(f"解析失败: {data}")
        raise Exception(f"API响应格式异常: {e}")
