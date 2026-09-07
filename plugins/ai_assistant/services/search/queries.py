import httpx
import json
import re
import math
from typing import Any, Tuple, Optional, List
from nonebot.log import logger
from pydantic import Field
from plugins.ai_assistant.config import StrictBaseModel, plugin_config
from plugins.ai_assistant.services.chat_service import call_chat_completion


def _cleanup_search_text(text: str) -> str:
    """清理用户文本，减少噪声，便于生成搜索 query。"""
    if not text:
        return ""

    t = text.strip()

    # 去掉 Markdown 代码块/行内代码，避免把整段代码丢进搜索
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"`[^`]*`", " ", t)

    # 去掉引用前缀
    t = re.sub(r"^>\s*", "", t, flags=re.MULTILINE)

    # 去掉多余空白
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_core_question(text: str) -> str:
    """
    从较长文本中提取“最像问题的一段”作为 core query。
    策略：优先取最后一个带问号的片段，否则取最后一句。
    """
    if not text:
        return ""

    # 按常见中文/英文标点切分
    parts = re.split(r"[。！？!?；;]\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return text.strip()

    # 优先找包含问号的原始片段
    m = re.findall(r"[^。！？!?]*[!?？][^。！？!?]*", text)
    m = [x.strip() for x in m if x.strip()]
    if m:
        return m[-1]

    # 否则取最后一句
    return parts[-1]


def _truncate_query(q: str, max_len: int) -> str:
    q = (q or "").strip()
    if not q:
        return ""
    if max_len and len(q) > max_len:
        return q[:max_len].strip()
    return q


def build_search_queries(raw_text: str, *, mode: str = "chat") -> List[str]:
    """
    启发式生成多条搜索 query（不调用模型）。
    mode:
      - chat: 偏向事实/技术问题检索
      - image: 偏向外观设定/参考资料检索
    """
    max_len = int(getattr(plugin_config.search, "query_max_len", 120) or 120)
    max_q = int(getattr(plugin_config.search, "num_queries", 3) or 3)

    cleaned = _cleanup_search_text(raw_text)
    core = _extract_core_question(cleaned)

    # 抽取英文/版本号/报错片段等，作为补充关键词
    english_terms = re.findall(r"[A-Za-z][A-Za-z0-9_\-./]{2,}", cleaned)
    versions = re.findall(r"\b\d+(?:\.\d+){1,3}\b", cleaned)

    # 可能的报错行（包含 error/exception/failed 等）
    err_lines = []
    for seg in re.split(r"[。！？!?]\s*|\n+", raw_text or ""):
        s = seg.strip()
        if not s:
            continue
        if re.search(
            r"(error|exception|failed|traceback|not found|无法|报错|错误)", s, re.I
        ):
            err_lines.append(s)
    err_snippet = err_lines[-1] if err_lines else ""

    # 去重并限制数量
    extras = []
    for x in english_terms + versions:
        x = x.strip()
        if x and x not in extras:
            extras.append(x)
    extras = extras[:6]

    queries: List[str] = []

    q1 = _truncate_query(core, max_len)
    if q1:
        queries.append(q1)

    if mode == "image" and core:
        for q in (
            f"{core} 外观 服装 发色 角色设定 立绘",
            f"{core} official visual character design outfit appearance",
        ):
            q = _truncate_query(q, max_len)
            if q and q not in queries:
                queries.append(q)

    if extras:
        q2 = _truncate_query(f"{core} {' '.join(extras[:3])}".strip(), max_len)
        if q2 and q2 not in queries:
            queries.append(q2)

    if err_snippet:
        q3 = _truncate_query(err_snippet, max_len)
        if q3 and q3 not in queries:
            queries.append(q3)

    return queries[:max_q]


async def rewrite_search_queries_with_llm(
    raw_text: str, *, mode: str = "chat"
) -> List[str]:
    """
    使用 LLM 对用户输入进行“检索 query 重写”，输出多条短 query。
    失败时返回空数组，由上层回退到启发式。
    """
    max_len = int(getattr(plugin_config.search, "query_max_len", 120) or 120)
    max_q = int(getattr(plugin_config.search, "num_queries", 3) or 3)

    system = (
        "你是一个搜索查询（web search query）重写器。"
        "你将用户的原始输入改写成适合搜索引擎的短 query。"
        "请输出严格的 JSON，不要输出多余文本。\n\n"
        "输出格式：\n"
        '{ "queries": ["query1", "query2", "query3"] }\n\n'
        "要求：\n"
        f"- queries 数量 1~{max_q}\n"
        f"- 每条 query 不超过 {max_len} 字符\n"
        "- 使用关键词/实体/版本号/错误码；去掉口语、铺垫、无关背景\n"
        "- 不要包含省略号“...”\n"
        "- 不要使用换行\n"
        "- 如果是技术问题，保留库名/平台/错误关键字\n"
        "- 如果是 image 模式，偏向角色/作品名/外观设定/立绘/官方视觉图/服装/配色\n"
    )

    user = f"mode={mode}\nraw={_cleanup_search_text(raw_text)}"

    try:
        result = await call_chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as e:
        logger.warning(f"LLM query rewrite 调用失败，回退启发式。err={e}")
        return []

    text = result.content.strip()
    # 去掉可能包裹的代码块
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        obj = json.loads(text)
        queries = obj.get("queries", [])
        if not isinstance(queries, list):
            return []
        out: List[str] = []
        for q in queries:
            if not isinstance(q, str):
                continue
            q = _truncate_query(q.replace("...", "").strip(), max_len)
            if q and q not in out:
                out.append(q)
        return out[:max_q]
    except Exception:
        logger.warning("LLM query rewrite 返回非 JSON，回退启发式。")
        return []


async def _resolve_search_queries(raw_text: str, *, mode: str) -> List[str]:
    queries = build_search_queries(raw_text, mode=mode)

    rewrite_enabled = bool(getattr(plugin_config.search, "query_rewrite", True))
    use_llm = bool(getattr(plugin_config.search, "query_rewrite_use_llm", True))
    trigger_len = int(
        getattr(plugin_config.search, "query_rewrite_llm_trigger_len", 200) or 200
    )

    if rewrite_enabled and use_llm and raw_text and len(raw_text) >= trigger_len:
        llm_q = await rewrite_search_queries_with_llm(raw_text, mode=mode)
        merged: List[str] = []
        for q in llm_q + queries:
            if q and q not in merged:
                merged.append(q)
        queries = merged[: int(getattr(plugin_config.search, "num_queries", 3) or 3)]

    if not queries:
        max_len = int(getattr(plugin_config.search, "query_max_len", 120) or 120)
        cleaned = _cleanup_search_text(raw_text)
        if cleaned:
            queries = [_truncate_query(cleaned, max_len)]

    return queries
