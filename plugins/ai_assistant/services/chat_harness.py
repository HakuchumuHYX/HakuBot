from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

from nonebot.log import logger

from ..config import plugin_config
from .search_service import format_chat_evidence_pack, web_chat_search_with_rewrite


SearchRunner = Callable[[str, str], Awaitable[Tuple[List[str], List[dict]]]]
EvidenceFormatter = Callable[..., str]


@dataclass
class ChatSearchDecision:
    mode: str
    search_text: str = ""
    reason: str = ""


@dataclass
class ChatHarnessResult:
    messages: list
    search_mode: str = "none"
    search_text: str = ""
    queries: List[str] = field(default_factory=list)
    evidence: str = ""
    search_error: Optional[str] = None


_FORWARD_TRANSCRIPT_HEADER = "【用户回复的合并转发聊天记录】"

_FRESH_PATTERNS = re.compile(
    r"("
    r"最新|今天|今日|昨天|昨日|明天|本周|本月|今年|近期|最近|实时|现任|新版|"
    r"版本|价格|报价|股价|汇率|天气|新闻|政策|法规|赛程|比分|排名|更新|发布|上线|"
    r"latest|current|today|now|recent|release|changelog"
    r")",
    re.I,
)

_AMBIGUOUS_TIME_PATTERNS = re.compile(r"(现在|当前|目前)")
_EXTERNAL_FACT_PATTERNS = re.compile(
    r"(谁|哪|什么|多少|几|版本|价格|报价|股价|汇率|天气|新闻|政策|法规|赛程|比分|排名|总统|主席|首相|CEO|发布|更新)"
)
_CHAT_SELF_PATTERNS = re.compile(r"^(你|妳|您|bot|机器人).{0,8}(现在|当前|目前)")

_DEEP_PATTERNS = re.compile(
    r"(联网|搜索|查一下|搜一下|来源|引用|官方|多来源|交叉验证|政策|法规|财报|CVE|漏洞|论文|研究)",
    re.I,
)


def build_runtime_context(
    *,
    now: Optional[datetime] = None,
    timezone_name: Optional[str] = None,
) -> str:
    tz_name = timezone_name or getattr(plugin_config.chat, "runtime_timezone", "Asia/Shanghai") or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "Asia/Shanghai"
        tz = ZoneInfo(tz_name)

    current = now.astimezone(tz) if now else datetime.now(tz)
    return (
        f"当前真实日期：{current.date().isoformat()}\n"
        f"当前本地时间：{current.strftime('%H:%M:%S')}\n"
        f"当前时区：{tz_name}\n"
        "以上时间信息由宿主系统在请求时提供，优先于模型内置知识。"
        "回答涉及今天、现在、今年、过去/未来日期判断时，必须以此为准。"
    )


def extract_search_text(content_list: list) -> str:
    texts: List[str] = []
    for item in content_list or []:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if text.startswith(_FORWARD_TRANSCRIPT_HEADER):
            continue
        texts.append(text)
    return " ".join(texts).strip()


def decide_chat_search(content_list: list, *, force_search: bool = False) -> ChatSearchDecision:
    search_text = extract_search_text(content_list)
    if not search_text:
        return ChatSearchDecision("none", "", "no_search_text")

    if force_search:
        return ChatSearchDecision("deep", search_text, "force_search")

    if not bool(getattr(plugin_config.search, "auto_search_enabled", True)):
        return ChatSearchDecision("none", search_text, "auto_search_disabled")

    auto_mode = (getattr(plugin_config.search, "auto_search_mode", "smart") or "smart").lower()
    if auto_mode == "off":
        return ChatSearchDecision("none", search_text, "auto_search_off")
    if auto_mode == "always":
        return ChatSearchDecision("quick", search_text, "auto_search_always")

    has_fresh_signal = bool(_FRESH_PATTERNS.search(search_text))
    if (
        not has_fresh_signal
        and _AMBIGUOUS_TIME_PATTERNS.search(search_text)
        and _EXTERNAL_FACT_PATTERNS.search(search_text)
        and not _CHAT_SELF_PATTERNS.search(search_text)
    ):
        has_fresh_signal = True

    if has_fresh_signal:
        if _DEEP_PATTERNS.search(search_text):
            return ChatSearchDecision("deep", search_text, "fresh_deep_keyword")
        return ChatSearchDecision("quick", search_text, "fresh_keyword")

    return ChatSearchDecision("none", search_text, "no_fresh_signal")


def build_chat_messages(
    content_list: list,
    *,
    decision: Optional[ChatSearchDecision] = None,
    runtime_context: Optional[str] = None,
    evidence_context: str = "",
    queries: Optional[List[str]] = None,
) -> list:
    decision = decision or decide_chat_search(content_list)
    runtime_context = runtime_context or build_runtime_context()

    messages = [
        {"role": "system", "content": plugin_config.chat.system_prompt},
        {"role": "system", "content": runtime_context},
    ]

    if evidence_context:
        queries_hint = " / ".join(queries or []) if queries else "（无）"
        messages.append(
            {
                "role": "system",
                "content": (
                    "你将收到一段【联网证据包】和【本次检索 query】。请严格遵守：\n"
                    "1) 涉及最新事实、版本、政策、新闻、报价、日期等，优先依据证据包，并用编号引用，例如 [1]。\n"
                    "2) 不得杜撰证据包中没有的来源、链接、标题、编号或事实。\n"
                    "3) 如果证据包没有覆盖问题关键点，请明确说明搜索结果未覆盖。\n"
                    "4) 如果证据包和模型内置知识冲突，以证据包和运行时日期为准。\n"
                    "\n【本次检索 query】\n"
                    + queries_hint
                    + "\n\n【联网证据包】\n"
                    + evidence_context
                ),
            }
        )
    elif decision.mode != "none":
        messages.append(
            {
                "role": "system",
                "content": (
                    "本次问题被判断为可能需要最新信息，但联网证据未成功获取。"
                    "如果答案依赖实时或最新事实，请明确说明无法确认最新信息。"
                ),
            }
        )

    messages.append({"role": "user", "content": content_list})
    return messages


async def _default_search_runner(search_text: str, mode: str) -> Tuple[List[str], List[dict]]:
    search_cfg = plugin_config.search
    if mode == "quick":
        return await web_chat_search_with_rewrite(
            search_text,
            max_results=int(getattr(search_cfg, "auto_search_quick_max_results", 3) or 3),
            search_depth="basic",
            include_raw_content=False,
        )
    return await web_chat_search_with_rewrite(
        search_text,
        max_results=int(getattr(search_cfg, "auto_search_deep_max_results", 5) or 5),
    )


async def prepare_chat_messages(
    content_list: list,
    *,
    force_search: bool = False,
    decision: Optional[ChatSearchDecision] = None,
    search_runner: Optional[SearchRunner] = None,
    evidence_formatter: EvidenceFormatter = format_chat_evidence_pack,
) -> ChatHarnessResult:
    decision = decision or decide_chat_search(content_list, force_search=force_search)
    queries: List[str] = []
    evidence = ""
    search_error = None

    if decision.mode != "none":
        runner = search_runner or _default_search_runner
        try:
            queries, search_payloads = await runner(decision.search_text, decision.mode)
            max_chars = None
            if decision.mode == "quick":
                max_chars = int(getattr(plugin_config.search, "auto_search_context_max_chars", 3000) or 3000)
            evidence = evidence_formatter(search_payloads, max_chars=max_chars)
        except Exception as e:
            search_error = str(e)
            logger.warning(f"自动联网检索失败: mode={decision.mode} text={decision.search_text!r} err={e}")

    messages = build_chat_messages(
        content_list,
        decision=decision,
        evidence_context=evidence,
        queries=queries,
    )
    return ChatHarnessResult(
        messages=messages,
        search_mode=decision.mode,
        search_text=decision.search_text,
        queries=queries,
        evidence=evidence,
        search_error=search_error,
    )
