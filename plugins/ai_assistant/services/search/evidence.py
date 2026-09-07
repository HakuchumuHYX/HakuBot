from typing import Optional, List
from plugins.ai_assistant.config import plugin_config
from plugins.ai_assistant.services.search import normalization as _normalization


def format_chat_evidence_pack(
    search_payloads: List[dict], *, max_chars: Optional[int] = None
) -> str:
    if not search_payloads:
        return "（联网搜索未返回结果）"

    if max_chars is None:
        max_chars = int(
            getattr(plugin_config.search, "chat_context_max_chars", 5000) or 5000
        )
    content_limit = int(
        getattr(plugin_config.search, "chat_content_max_chars", 700) or 700
    )
    raw_limit = int(
        getattr(plugin_config.search, "chat_raw_content_max_chars", 1200) or 1200
    )

    lines: List[str] = []
    answer_seen = set()
    for payload in search_payloads:
        answer = _normalization._truncate_text(payload.get("answer") or "", 900)
        if not answer or answer in answer_seen:
            continue
        answer_seen.add(answer)
        query = payload.get("query") or ""
        lines.append(f"【Tavily 摘要：{query}】\n{answer}")

    source_idx = 1
    for payload in search_payloads:
        for result in payload.get("results", []) or []:
            title = result.get("title") or ""
            url = result.get("url") or ""
            score = result.get("score")
            content = _normalization._truncate_text(
                result.get("content") or "", content_limit
            )
            raw_content = _normalization._truncate_text(
                result.get("raw_content") or "", raw_limit
            )

            header = f"[{source_idx}] {title}".strip()
            if score is not None:
                header += f" score={score}"
            lines.append(header)
            if url:
                lines.append(url)
            if content:
                lines.append(f"摘要：{content}")
            if raw_content:
                lines.append(f"正文片段：{raw_content}")
            source_idx += 1

    if source_idx == 1 and not lines:
        return "（联网搜索未返回结果）"

    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n（证据包过长，已截断）"
    return text
