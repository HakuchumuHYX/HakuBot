import httpx
import json
import re
import math
from typing import Tuple, Optional, List
from nonebot.log import logger
from ..config import plugin_config
from .chat_service import call_chat_completion

def _strip_control_chars(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", text or "")

async def tavily_search(
    query: str,
    *,
    max_results: Optional[int] = None,
    search_depth: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> List[dict]:
    """
    使用 Tavily 进行联网搜索（手动命令触发）。
    返回结果格式：[{title, url, content}]
    """
    api_key = getattr(plugin_config.search, "tavily_api_key", None)
    if not api_key:
        raise Exception("未配置 tavily_api_key，请在 plugins/ai_assistant/config.json 中填写。")

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": int(
            max_results
            if max_results is not None
            else (getattr(plugin_config.search, "max_results", 5) or 5)
        ),
        "search_depth": (
            search_depth
            if search_depth is not None
            else (getattr(plugin_config.search, "depth", "basic") or "basic")
        ),
        "include_answer": False,
        "include_raw_content": False,
    }

    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    async with httpx.AsyncClient(
        proxy=plugin_config.proxy,
        timeout=plugin_config.timeout,
    ) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        if resp.status_code != 200:
            raise Exception(f"Tavily API Error {resp.status_code}: {resp.text}")
        data = resp.json()

    results = data.get("results", []) or []
    normalized: List[dict] = []
    for item in results:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or item.get("snippet") or "").strip()
        if not url and not content and not title:
            continue
        normalized.append(
            {
                "title": _strip_control_chars(title),
                "url": _strip_control_chars(url),
                "content": _strip_control_chars(content),
            }
        )

    return normalized

def format_search_results(results: List[dict], max_chars: int = 2500) -> str:
    """
    将 Tavily 搜索结果格式化为可注入 messages 的文本，包含可引用的链接。
    """
    if not results:
        return "（联网搜索未返回结果）"

    lines: List[str] = []
    for idx, r in enumerate(results, start=1):
        title = r.get("title") or ""
        url = r.get("url") or ""
        content = r.get("content") or ""
        snippet = content.replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        lines.append(f"[{idx}] {title}\n{url}\n摘要：{snippet}")

    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n（搜索结果过长，已截断）"
    return text

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
        if re.search(r"(error|exception|failed|traceback|not found|无法|报错|错误)", s, re.I):
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

    if extras:
        q2 = _truncate_query(f"{core} {' '.join(extras[:3])}".strip(), max_len)
        if q2 and q2 not in queries:
            queries.append(q2)

    if err_snippet:
        q3 = _truncate_query(err_snippet, max_len)
        if q3 and q3 not in queries:
            queries.append(q3)

    if mode == "image":
        # 生图联网：更偏向“设定/立绘/参考图”
        if queries:
            q_img = _truncate_query(f"{queries[0]} 设定 立绘", max_len)
            if q_img and q_img not in queries:
                queries.append(q_img)

    return queries[:max_q]

async def rewrite_search_queries_with_llm(raw_text: str, *, mode: str = "chat") -> List[str]:
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
        "- 如果是 image 模式，偏向角色/作品名/外观设定/立绘/参考\n"
    )

    user = f"mode={mode}\nraw={_cleanup_search_text(raw_text)}"

    try:
        content, _, _ = await call_chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=256,
        )
    except Exception as e:
        logger.warning(f"LLM query rewrite 调用失败，回退启发式。err={e}")
        return []

    text = (content or "").strip()
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

async def web_search_with_rewrite(raw_text: str, *, mode: str = "chat") -> Tuple[List[str], List[dict]]:
    """
    统一入口：对 raw_text 做 query 提炼/重写 → 多 query 搜索 → 合并去重。
    返回：(queries, merged_results)
    """
    # 1) 先启发式
    queries = build_search_queries(raw_text, mode=mode)

    # 2) 需要时用 LLM 重写（B 方案）
    rewrite_enabled = bool(getattr(plugin_config.search, "query_rewrite", True))
    use_llm = bool(getattr(plugin_config.search, "query_rewrite_use_llm", True))
    trigger_len = int(getattr(plugin_config.search, "query_rewrite_llm_trigger_len", 200) or 200)

    if rewrite_enabled and use_llm and raw_text and len(raw_text) >= trigger_len:
        llm_q = await rewrite_search_queries_with_llm(raw_text, mode=mode)
        # 组合：优先使用 LLM 的 query；不足再用启发式补齐
        merged: List[str] = []
        for q in llm_q + queries:
            if q and q not in merged:
                merged.append(q)
        queries = merged[: int(getattr(plugin_config.search, "num_queries", 3) or 3)]

    # 3) 如果仍然没有 query，兜底用清理后的原文截断
    if not queries:
        max_len = int(getattr(plugin_config.search, "query_max_len", 120) or 120)
        cleaned = _cleanup_search_text(raw_text)
        if cleaned:
            queries = [_truncate_query(cleaned, max_len)]

    # 4) 多 query 搜索 + 合并去重
    total_max = int(getattr(plugin_config.search, "max_results", 5) or 5)
    if total_max < 1:
        total_max = 5

    per_query = max(1, int(math.ceil(total_max / max(1, len(queries)))))

    merged_results: List[dict] = []
    seen_urls = set()

    for q in queries:
        try:
            rs = await tavily_search(q, max_results=per_query)
        except Exception as e:
            logger.warning(f"Tavily 搜索失败: query={q} err={e}")
            continue

        for item in rs:
            url = (item.get("url") or "").strip()
            key = url or (item.get("title") or "") + (item.get("content") or "")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            merged_results.append(item)

            if len(merged_results) >= total_max:
                break
        if len(merged_results) >= total_max:
            break

    return queries, merged_results
