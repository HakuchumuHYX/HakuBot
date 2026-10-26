"""chat 的工具循环、提示词和回复链历史"""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from nonebot import logger

from utils.llm.client import LLMClientConfig, TokenUsage, chat_completion
from utils.logging import get_exc_desc
from plugins.ai_assistant.config import plugin_config
from plugins.ai_assistant.search import WEB_SEARCH_TOOL, format_results, web_search

MAX_SEARCHES = 6
HISTORY_MAX_ROUNDS = 10
TIMEZONE = "Asia/Shanghai"

TOOL_GUIDE = (
    "你可以用 web_search 工具联网搜索。问题涉及最新动态、具体数据，"
    "或者你不确定的事实时，先搜索再回答；常识、闲聊、创作类问题直接回答。"
    "用到搜索结果时，在回答末尾列出实际引用的来源（标题和域名）。"
    "没有查到的信息如实说明，不要编造来源。"
)
# 超出预算时回填工具结果让模型收尾；实测用 tool_choice="none" 或去掉 tools
# 强制作答，模型会把伪工具调用 JSON 写进正文
BUDGET_EXHAUSTED = (
    "已达到本次搜索次数上限，未执行搜索。"
    "请基于已有信息直接回答，并说明哪些信息未能确认。"
)

SearchFunc = Callable[[str], Awaitable[str]]


@dataclass
class LoopResult:
    content: str
    model: str
    usage: TokenUsage
    elapsed: float
    searches: int


def chat_llm_config(model: str | None = None) -> LLMClientConfig:
    return LLMClientConfig(
        api_key=plugin_config.api_key,
        base_url=plugin_config.base_url,
        model=model or plugin_config.chat.model,
        timeout=plugin_config.timeout,
        proxy=plugin_config.proxy,
        max_tokens=plugin_config.chat.max_tokens,
        extra_body=plugin_config.chat.extra_body,
    )


def runtime_context() -> str:
    now = datetime.now(ZoneInfo(TIMEZONE))
    weekday = "一二三四五六日"[now.weekday()]
    return (
        f"当前时间：{now:%Y-%m-%d %H:%M} 星期{weekday}（{TIMEZONE}），由系统提供；"
        "判断今天、最近、今年等时间时以此为准，不要用训练数据里的日期。"
    )


async def chat_search(query: str) -> str:
    return format_results(await web_search(query, include_images=False))


async def _run_search(search: SearchFunc, query: str) -> str:
    try:
        return await search(query)
    except Exception as exc:
        logger.warning(f"搜索失败 {query!r}: {get_exc_desc(exc)}")
        return f"搜索失败：{get_exc_desc(exc)}"


async def _constant(text: str) -> str:
    return text


def _parse_query(call: dict) -> str | None:
    """工具参数是模型输出，在这里校验一次"""
    function = call["function"]
    if function["name"] != WEB_SEARCH_TOOL["function"]["name"]:
        return None
    try:
        query = json.loads(function["arguments"]).get("query")
    except (json.JSONDecodeError, AttributeError):
        return None
    return query.strip() if isinstance(query, str) and query.strip() else None


async def run_tool_loop(
    messages: list, *, search: SearchFunc, max_searches: int
) -> LoopResult:
    """让模型自己决定要不要搜索，直到它给出最终文本。messages 会被追加工具轮次。"""
    config = chat_llm_config()
    usage = TokenUsage()
    searches = 0
    rejected_round = False
    started = time.perf_counter()

    while True:
        result = await chat_completion(
            config,
            messages,
            temperature=plugin_config.chat.temperature,
            tools=[WEB_SEARCH_TOOL],
            tool_choice="auto",
        )
        usage.prompt_tokens += result.usage.prompt_tokens
        usage.completion_tokens += result.usage.completion_tokens
        usage.total_tokens += result.usage.total_tokens

        if not result.tool_calls:
            content = result.content.strip()
            if not content:
                raise ValueError("模型返回成功，但响应中没有文本内容。")
            return LoopResult(
                content=content,
                model=result.model,
                usage=usage,
                elapsed=time.perf_counter() - started,
                searches=searches,
            )

        # 预算用完后整轮回填过一次「已达上限」，模型还在调用就不再给机会。
        # 只有部分调用被拒的那轮不算：模型常一轮并行发很多个，拒掉一部分后它会合理地补搜
        if searches >= max_searches:
            if rejected_round:
                raise RuntimeError("模型在搜索次数用完后仍在请求搜索，没能给出回答。")
            rejected_round = True

        messages.append(
            {
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": result.tool_calls,
            }
        )
        tasks = []
        for call in result.tool_calls:
            query = _parse_query(call)
            if searches >= max_searches:
                tasks.append(_constant(BUDGET_EXHAUSTED))
            elif query is None:
                # 也计入预算，免得模型反复发无效调用时一直循环
                searches += 1
                logger.warning(f"忽略无效的工具调用: {call['function']}")
                tasks.append(_constant("工具调用参数无效，未执行。"))
            else:
                searches += 1
                logger.debug(f"web_search: {query}")
                tasks.append(_run_search(search, query))

        outputs = await asyncio.gather(*tasks)
        messages.extend(
            {"role": "tool", "tool_call_id": call["id"], "content": output}
            for call, output in zip(result.tool_calls, outputs)
        )


def build_chat_messages(history: list, user_content: list) -> list:
    system = "\n\n".join(
        [plugin_config.chat.system_prompt.strip(), runtime_context(), TOOL_GUIDE]
    )
    messages = [{"role": "system", "content": system}]
    bottom_line = plugin_config.chat.bottom_line.strip()
    if bottom_line:
        messages.append({"role": "system", "content": bottom_line})
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages


# (会话, bot 回答的 message_id) → (过期时间, 历史)。历史只含 user 轮和
# assistant 最终文本；旧节点不覆盖，回复不同节点就是不同支线。只放内存，重启即清空
_threads: dict[tuple[str, int], tuple[float, list]] = {}


def load_thread(session: str, message_id: int) -> list | None:
    entry = _threads.get((session, message_id))
    if entry is None or entry[0] < time.time():
        return None
    return entry[1]


def save_thread(session: str, message_id: int, history: list) -> None:
    now = time.time()
    for key in [key for key, (expires, _) in _threads.items() if expires < now]:
        del _threads[key]
    expires = now + plugin_config.chat.history_ttl_minutes * 60
    _threads[(session, message_id)] = (expires, history[-HISTORY_MAX_ROUNDS * 2 :])
