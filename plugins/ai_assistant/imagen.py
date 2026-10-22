"""生图请求，以及生图联网的「搜集 → 看图 → 生图」流程"""

import asyncio
import base64
import json
import re
from dataclasses import dataclass

from nonebot import logger

from utils.llm.client import (
    LLMClientConfig,
    chat_completion,
    image_edit,
    image_generation,
)
from utils.logging import get_exc_desc
from utils.network.http import download_bytes
from plugins.ai_assistant.chat import chat_llm_config, run_tool_loop, runtime_context
from plugins.ai_assistant.config import plugin_config
from plugins.ai_assistant.search import SearchImage, format_results, web_search
from plugins.ai_assistant.utils import (
    REFERENCE_IMAGE_MAX_SIZE,
    extract_pure_text,
    image_data_url,
    parse_data_url,
)

MAX_SEARCHES = 4
# 筛图只需看清是不是同一主体；实测 3 张 1024px 一次要 78s
VISION_IMAGE_SIZE = 512
# 先下模型列出的候选图，再按出现顺序补足到这个数：网图常 403，真正筛图靠看图那一步
MAX_CANDIDATES = 6
# 看图时模型推理长短很不稳定，实测 5s～173s 都有，chat 的超时不够用
VISION_TIMEOUT = 180.0
MAX_WEB_REFS = 2

_IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

COLLECT_GUIDE = (
    "你在为一次 AI 生图收集画面主体的外观资料。先判断用户要画的主体是谁或是什么，"
    "再用 web_search 搜索它的外观，官方设定、官方立绘或照片优先；"
    "主体是用户原创、或者纯粹的风格和场景描述时，不必搜索。\n"
    "搜集完后用几句话总结：主体是谁或是什么，以及关键外观特征（发型发色、服装、配色、标志性物品、建筑结构等）。\n"
    "最后单独一行写「候选图：」，列出搜索结果里从描述看最可能是该主体官方设定图或清晰照片的编号，"
    "例如「候选图：I3, I5」；没有合适的就写「候选图：无」。"
)

VISION_GUIDE = """你在为 AI 生图挑选参考图并撰写画面描述。

【用户需求】
{request}

【联网资料总结】
{summary}

下面是候选图片，每张前面标着编号。{user_note}请完成两件事：
1. selected：从网图（I 开头的编号）里挑出确认就是用户要画的那个主体的图，最多 {max_refs} 张。官方设定、官方立绘或实拍照片优先，清晰、能看全外观的优先；外观明显偏离官方的二创图不选，看不准是不是同一主体也不选，宁缺毋滥。
2. prompt：写一段直接给画图模型看的完整画面描述。主体外观（发型发色、五官特征、服装、配色、配饰、建筑结构等）按选中的图里实际看到的写；动作、构图、场景和画风按用户需求来设计，不要照搬候选图里的姿势和画面。不要提「参考图」「编号」这类字眼。

只输出一个 JSON 对象，不要输出其他内容：
{{"selected": ["I3"], "prompt": "..."}}"""

REFERENCE_NOTE = (
    "网上找到的参考图只用于保持主体外观一致，请按下面的描述重新构图，"
    "不要沿用这些参考图的姿势、背景和画面布局。"
)


@dataclass
class WebDrawPlan:
    prompt: str
    references: list[str]  # data URL，用户的图在前
    searches: int
    web_refs: int
    vision_failed: bool


def _image_llm_config() -> LLMClientConfig:
    return LLMClientConfig(
        api_key=plugin_config.api_key,
        base_url=plugin_config.base_url,
        model=plugin_config.image.model,
        timeout=plugin_config.image.timeout,
        proxy=plugin_config.proxy,
        # 生图不重试：超时的请求上游可能还在出图，重试会重复出图
        max_retries=0,
    )


def _decode_reference(data_url: str, index: int) -> tuple[str, bytes, str]:
    mime, encoded = parse_data_url(data_url)
    extension = _IMAGE_EXTENSIONS.get(mime.lower())
    if not extension:
        raise ValueError(f"不支持的参考图格式: {mime}")
    return f"ref{index}.{extension}", base64.b64decode(encoded), mime


async def request_image(
    description: str, references: list[str], *, note: str = ""
) -> str:
    """有参考图走 images/edits，没有走 images/generations；返回可直接发送的图片地址"""
    parts = [
        plugin_config.image.prompt_prefix.strip(),
        note,
        f"【画面需求】\n{description}",
        plugin_config.image.bottom_line.strip(),
    ]
    prompt = "\n\n".join(part for part in parts if part)
    config = _image_llm_config()
    options = {
        "prompt": prompt,
        "model": plugin_config.image.model,
        "size": plugin_config.image.size,
        "quality": plugin_config.image.quality,
    }

    if references:
        images = [
            _decode_reference(url, index)
            for index, url in enumerate(references, start=1)
        ]
        logger.debug(f"正在请求图生图 (images/edits)，参考图 {len(images)} 张")
        result = await image_edit(config, images=images, **options)
    else:
        logger.debug("正在请求文生图 (images/generations)")
        result = await image_generation(config, **options)
    return result.image_url


def _user_images(content_list: list) -> list[str]:
    return [
        item["image_url"]["url"] for item in content_list if item["type"] == "image_url"
    ]


async def draw(content_list: list) -> str:
    description = extract_pure_text(content_list).strip()
    references = _user_images(content_list)
    if not description:
        description = "Use the provided image as a visual reference."
    return await request_image(description, references)


def _vision_data_url(data_url: str) -> str:
    _, encoded = parse_data_url(data_url)
    return image_data_url(
        base64.b64decode(encoded), max_size=VISION_IMAGE_SIZE, preserve_format=False
    )


def _split_candidates(
    text: str, labels: dict[str, SearchImage]
) -> tuple[str, list[str]]:
    """拆出总结和「候选图：」那一行里出现过的有效编号"""
    match = re.search(r"^\s*候选图[:：](.*)$", text, re.MULTILINE)
    if match is None:
        return text.strip(), []
    summary = (text[: match.start()] + text[match.end() :]).strip()
    picked = [label for label in re.findall(r"I\d+", match.group(1)) if label in labels]
    return summary, list(dict.fromkeys(picked))


async def _download_candidate(
    label: str, url: str
) -> tuple[str, tuple[bytes, str]] | None:
    """返回 (编号, (原图, 给视觉模型的小图))；fandom 等站点常 403，失败就跳过"""
    try:
        raw = await download_bytes(
            url, proxy=plugin_config.proxy, timeout=20, attempts=1
        )
        # 有的站点拦截时返回 200 的 HTML，这一步解码失败也算下载失败
        preview = image_data_url(raw, max_size=VISION_IMAGE_SIZE, preserve_format=False)
    except Exception as exc:
        logger.warning(f"候选图 {label} 下载失败，已跳过: {get_exc_desc(exc)}")
        return None
    return label, (raw, preview)


def _parse_vision(text: str, allowed: set[str]) -> tuple[list[str], str]:
    """视觉模型的输出是外部输入，在这里校验一次；格式不对就抛 ValueError"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError(f"没有 JSON 对象: {text[:200]!r}")
    data = json.loads(match.group(0))
    selected, prompt = data.get("selected"), data.get("prompt")
    if not isinstance(selected, list) or not all(
        isinstance(label, str) and label in allowed for label in selected
    ):
        raise ValueError(f"selected 无效: {selected!r}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt 为空")
    return selected, prompt.strip()


async def research(content_list: list) -> WebDrawPlan:
    """生图联网的前两段：搜集资料、看图筛选并写好画面描述"""
    request = extract_pure_text(content_list).strip()
    user_images = _user_images(content_list)

    # ① 搜集：模型自己决定搜什么，搜索结果里的图片统一编号
    labels: dict[str, SearchImage] = {}

    async def search(query: str) -> str:
        return format_results(await web_search(query, include_images=True), labels)

    user_content = [{"type": "text", "text": request}] + [
        {"type": "image_url", "image_url": {"url": _vision_data_url(url)}}
        for url in user_images
    ]
    messages = [
        {"role": "system", "content": f"{COLLECT_GUIDE}\n\n{runtime_context()}"},
        {"role": "user", "content": user_content},
    ]
    loop = await run_tool_loop(messages, search=search, max_searches=MAX_SEARCHES)
    summary, picked = _split_candidates(loop.content, labels)
    candidates = list(dict.fromkeys(picked + list(labels)))[:MAX_CANDIDATES]
    fallback = f"{request}\n\n【联网资料】\n{summary}"

    # ② 看图：并发下载候选图，一次视觉调用完成筛图和写描述
    downloads = await asyncio.gather(
        *(_download_candidate(label, labels[label].url) for label in candidates)
    )
    web_images = dict(item for item in downloads if item is not None)
    if not web_images and not user_images:
        return WebDrawPlan(fallback, [], loop.searches, 0, vision_failed=False)

    max_refs = 1 if user_images else MAX_WEB_REFS
    user_note = (
        f"U 开头的是用户自己提供的图（共 {len(user_images)} 张），按用户需求使用。"
        if user_images
        else ""
    )
    vision_content = [
        {
            "type": "text",
            "text": VISION_GUIDE.format(
                request=request, summary=summary, user_note=user_note, max_refs=max_refs
            ),
        }
    ]
    numbered = [
        (f"U{index}", _vision_data_url(url))
        for index, url in enumerate(user_images, start=1)
    ] + [(label, preview) for label, (_, preview) in web_images.items()]
    for label, url in numbered:
        vision_content.append({"type": "text", "text": f"[{label}]"})
        vision_content.append({"type": "image_url", "image_url": {"url": url}})

    vision_config = chat_llm_config()
    vision_config.timeout = VISION_TIMEOUT
    result = await chat_completion(
        vision_config,
        [{"role": "user", "content": vision_content}],
        temperature=plugin_config.chat.temperature,
    )
    try:
        selected, prompt = _parse_vision(result.content, set(web_images))
    except ValueError as exc:  # 含 json.JSONDecodeError
        logger.warning(f"视觉提炼结果无效，改为只用文字资料: {get_exc_desc(exc)}")
        return WebDrawPlan(fallback, user_images, loop.searches, 0, vision_failed=True)

    web_refs = [
        image_data_url(
            web_images[label][0],
            max_size=REFERENCE_IMAGE_MAX_SIZE,
            preserve_format=True,
        )
        for label in list(dict.fromkeys(selected))[:max_refs]
    ]
    return WebDrawPlan(
        prompt,
        user_images + web_refs,
        loop.searches,
        len(web_refs),
        vision_failed=False,
    )


async def draw_from_plan(plan: WebDrawPlan) -> str:
    note = REFERENCE_NOTE if plan.web_refs else ""
    return await request_image(plan.prompt, plan.references, note=note)
