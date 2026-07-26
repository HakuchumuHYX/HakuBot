"""bili_dyn_sub 动态解析：feed/space 的单个 item(dict) → ParsedDynamic。

对应设计文档 §7（11 种 major → 6 类分类映射）、§4.2（置顶识别）、§5.1（文字模板所需字段）。

解析逻辑与字段语义借鉴 nonebot-bison (MIT, Copyright (c) 2021 felinae98)：
platform/bilibili/platforms.py 的 `_do_get_category` / `pre_parse_by_mojar` / `parse`，
utils/__init__.py 的 `text_similarity` / `decode_unicode_escapes`。
差别：不引 pydantic 模型（其 models.py 403 行），一律 dict 取值 + 兜底默认值，
schema 小改（B 站每年 1-3 次）时表现为「字段变空」而非 ValidationError 整条丢失。

设计约束（宁可丑不可漏）：本模块是**纯函数、无 IO、无网络**，可安全在事件循环里调用；
任意缺字段 / 类型不符 / 未知 type 都降级（`parse_degraded=True`）而不抛异常；
只有 `id_str` 缺失才返回 None（该条无法去重，跳过）。

关于 url 字段（设计文档 §5.1 与 §7 的两处表述在此合并）：
- `url`：统一为 `https://t.bilibili.com/{dyn_id}`，渲染层 "详情:" 用它（占位/已删除源为空串）；
- `major_url`：各 major 自带的跳转链接（视频/专栏/直播/课程等，已补 https 前缀），
  与 bison 的 `post.url` 等价，渲染层若要逐字符复刻 bison 的视频动态链接可用它。
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional
from urllib.parse import urlsplit, urlunsplit

from ..utils.tools import get_logger

logger = get_logger("bili_dyn_sub.parser")

# ---------------------------------------------------------------- 常量

# 动态类型 → 分类（设计文档 §7；与 bison `_do_get_category` 一致）
CATEGORY_MAP: dict[str, int] = {
    "DYNAMIC_TYPE_DRAW": 1,
    "DYNAMIC_TYPE_COMMON_SQUARE": 1,
    "DYNAMIC_TYPE_COMMON_VERTICAL": 1,
    "DYNAMIC_TYPE_ARTICLE": 2,
    "DYNAMIC_TYPE_AV": 3,
    "DYNAMIC_TYPE_WORD": 4,
    "DYNAMIC_TYPE_FORWARD": 5,
    "DYNAMIC_TYPE_LIVE": 6,
    "DYNAMIC_TYPE_LIVE_RCMD": 6,
}

# 分类名（与 bison `Bilibili.categories` 一致，供命令回显使用）
CATEGORY_NAMES: dict[int, str] = {
    1: "一般动态",
    2: "专栏文章",
    3: "视频",
    4: "纯文字",
    5: "转发",
    6: "直播推送",
}

TYPE_FORWARD = "DYNAMIC_TYPE_FORWARD"
TYPE_NONE = "DYNAMIC_TYPE_NONE"

# 跳过但仍要推进 seen 的类型（设计文档 §4.2），否则每轮重复处理
SKIP_TYPES: frozenset[str] = frozenset(
    {
        "DYNAMIC_TYPE_LIVE_RCMD",
        "DYNAMIC_TYPE_LIVE",
        "DYNAMIC_TYPE_AD",
        "DYNAMIC_TYPE_BANNER",
        # 顶层就是「已被删除的动态」：bison 在 get_sub_list 里直接
        # `[item for item in items if item.type != "DYNAMIC_TYPE_NONE"]` 丢掉，
        # 若不跳过会推出一条只有「源动态已被删除」的空动态（与 bison 输出不一致）。
        TYPE_NONE,
    }
)

# 源动态被删除时的兜底文案（B 站正常会在 major.none.tips 给出同义文本）
DELETED_SOURCE_TIPS = "源动态已被删除"

# 视频动态的「动态正文 / 视频简介」拼接分隔线（照抄 bison `_text_process`）
_VIDEO_TEXT_SPLIT = "\n=================\n"
# difflib 是 O(n²) 量级，超长文本只取前缀比较，避免在事件循环里做重活
_SIMILARITY_MAX_CHARS = 1000
# 转发只递归一层（B 站不存在 orig 里再套 orig，多一层视为脏数据）
_MAX_REPOST_DEPTH = 1

_ESCAPE_RE = re.compile(r"\\[rnt]|\\u[0-9a-fA-F]{4}")


# ---------------------------------------------------------------- 数据结构


@dataclass
class ParsedDynamic:
    """解析后的动态（渲染层/调度层只读此结构）"""

    dyn_id: str = ""
    uid: str = ""
    category: int = 0
    dyn_type: str = ""
    is_pinned: bool = False
    pub_ts: int = 0
    nickname: str = ""
    title: str = ""
    content: str = ""
    pics: list[str] = field(default_factory=list)
    url: str = ""
    repost: Optional["ParsedDynamic"] = None
    is_deleted_source: bool = False
    parse_degraded: bool = False
    major_url: str = ""
    """major 自带跳转链接（视频/专栏/直播等），可能为空；见模块文档"""


class _MajorParsed(NamedTuple):
    """单个 major 抽取出的四元组 + 是否降级"""

    title: str
    content: str
    pics: list[str]
    url: str
    degraded: bool = False


# ---------------------------------------------------------------- 基础工具


def _dget(obj: Any, key: str, default: Any = None) -> Any:
    """安全取字典字段：非 dict、缺 key、值为 None 一律回落 default"""
    if isinstance(obj, dict):
        value = obj.get(key, default)
        return default if value is None else value
    return default


def _as_str(value: Any) -> str:
    """任意值转字符串；None 转空串（dict/list 视为脏数据，同样回空串）"""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: Any, default: int = 0) -> int:
    """任意值转 int，失败回落 default"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_url(raw: Any, *, drop_query: bool = False) -> str:
    """补全链接协议：`//xxx` / `http://xxx` / 裸域名 → `https://xxx`；失败回空串"""
    url = _as_str(raw).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("http://"):
        url = f"https://{url[len('http://') :]}"
    elif not url.startswith("https://"):
        url = f"https://{url.lstrip('/')}"
    if drop_query:
        try:
            parts = urlsplit(url)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except ValueError as e:
            logger.debug(f"链接 {url!r} 无法拆解 query，按原样使用: {e!r}")
    return url


def _decode_escapes(text: str) -> str:
    """解码 \\r \\n \\t \\uXXXX 等转义序列（等价 bison `decode_unicode_escapes`）"""
    if not text or "\\" not in text:
        return text

    def _sub(match: "re.Match[str]") -> str:
        try:
            return bytes(match.group(0), "utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, ValueError):
            return match.group(0)

    return _ESCAPE_RE.sub(_sub, text)


def _pic_urls(items: Any, key: str) -> list[str]:
    """从 [{key: url}, ...] 抽图片链接，跳过脏项"""
    if not isinstance(items, (list, tuple)):
        return []
    return [url for url in (_as_str(_dget(item, key, "")).strip() for item in items) if url]


def _text_similarity(str1: str, str2: str) -> float:
    """最长公共子序列相似度（照抄 bison `text_similarity`，空串返回 0 而非抛异常）"""
    if not str1 or not str2:
        return 0.0
    left, right = str1[:_SIMILARITY_MAX_CHARS], str2[:_SIMILARITY_MAX_CHARS]
    matcher = difflib.SequenceMatcher(None, left, right)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / min(len(left), len(right))


def _process_video_text(dynamic: str, desc: str, title: str) -> tuple[str, str]:
    """视频动态的标题/正文去重拼接（逐步照抄 bison `_text_process`，保证推送文案 1:1）"""
    title_similarity = _text_similarity(title, desc[: len(title)]) if title and desc else 0.0
    if title_similarity > 0.9:
        # 简介开头就是标题，去掉重复部分
        desc = desc[len(title) :].lstrip()
    content_similarity = _text_similarity(dynamic, desc) if dynamic and desc else 0.0
    if content_similarity > 0.8:
        # 动态正文与视频简介高度重合，取更长的那份
        return title, desc if len(dynamic) < len(desc) else dynamic
    return title, desc + (f"{_VIDEO_TEXT_SPLIT}{dynamic}" if dynamic else "")


# ---------------------------------------------------------------- major 抽取


def _parse_major(item: dict, dyn_id: str) -> _MajorParsed:
    """按 major 类型抽取 (title, content, pics, url)；未知/缺失走通用兜底"""
    dyn_mod = _dget(_dget(item, "modules", {}), "module_dynamic", {})
    desc_text = _decode_escapes(_as_str(_dget(_dget(dyn_mod, "desc", {}), "text", "")))
    major = _dget(dyn_mod, "major")
    if not isinstance(major, dict):
        # 纯文字 / 转发（无 major）：正文取 modules.module_dynamic.desc.text
        return _MajorParsed("", desc_text, [], "")

    major_type = _as_str(_dget(major, "type", ""))
    if major_type == "MAJOR_TYPE_ARCHIVE":  # 视频
        archive = _dget(major, "archive", {})
        title, content = _process_video_text(
            desc_text,
            _decode_escapes(_as_str(_dget(archive, "desc", ""))),
            _as_str(_dget(archive, "title", "")),
        )
        return _MajorParsed(title, content, _one_pic(archive, "cover"), _normalize_url(_dget(archive, "jump_url")))

    if major_type == "MAJOR_TYPE_OPUS":  # 通用图文（itemOpusStyle 下的专栏/图文动态）
        opus = _dget(major, "opus", {})
        summary = _dget(opus, "summary", {})
        content = _as_str(_dget(summary, "text", "")) or _rich_text(_dget(summary, "rich_text_nodes"))
        return _MajorParsed(
            _as_str(_dget(opus, "title", "")),
            _decode_escapes(content) or desc_text,
            _pic_urls(_dget(opus, "pics"), "url"),
            _normalize_url(_dget(opus, "jump_url")),
        )

    if major_type == "MAJOR_TYPE_ARTICLE":  # 专栏文章（旧结构）
        article = _dget(major, "article", {})
        covers = _pic_urls_from_str_list(_dget(article, "covers"))
        return _MajorParsed(
            _as_str(_dget(article, "title", "")),
            _decode_escapes(_as_str(_dget(article, "desc", ""))),
            covers,
            _normalize_url(_dget(article, "jump_url")),
        )

    if major_type == "MAJOR_TYPE_DRAW":  # 图文动态
        draw = _dget(major, "draw", {})
        return _MajorParsed("", desc_text, _pic_urls(_dget(draw, "items"), "src"), "")

    if major_type == "MAJOR_TYPE_LIVE_RCMD":  # 直播推荐卡（content 是 JSON 文本）
        return _parse_live_rcmd(_dget(major, "live_rcmd", {}), desc_text)

    if major_type == "MAJOR_TYPE_LIVE":  # 直播间卡片
        live = _dget(major, "live", {})
        first, second = _as_str(_dget(live, "desc_first", "")), _as_str(_dget(live, "desc_second", ""))
        return _MajorParsed(
            _as_str(_dget(live, "title", "")),
            f"{first}\n{second}",
            _one_pic(live, "cover"),
            _normalize_url(_dget(live, "jump_url")),
        )

    if major_type in ("MAJOR_TYPE_PGC", "MAJOR_TYPE_PGC_UNION"):  # 番剧
        pgc = _dget(major, "pgc", {})
        return _MajorParsed(
            _as_str(_dget(pgc, "title", "")), "", _one_pic(pgc, "cover"), _normalize_url(_dget(pgc, "jump_url"))
        )

    if major_type == "MAJOR_TYPE_COMMON":  # 官方活动/会员购/漫画等通用卡
        common = _dget(major, "common", {})
        return _MajorParsed(
            _as_str(_dget(common, "title", "")),
            _decode_escapes(_as_str(_dget(common, "desc", ""))),
            _one_pic(common, "cover"),
            _normalize_url(_dget(common, "jump_url")),
        )

    if major_type == "MAJOR_TYPE_COURSES":  # 课程
        courses = _dget(major, "courses", {})
        sub_title, desc = _as_str(_dget(courses, "sub_title", "")), _as_str(_dget(courses, "desc", ""))
        return _MajorParsed(
            _as_str(_dget(courses, "title", "")),
            f"{sub_title}\n{desc}",
            _one_pic(courses, "cover"),
            _normalize_url(_dget(courses, "jump_url")),
        )

    if major_type == "MAJOR_TYPE_NONE":  # 源动态已被删除
        tips = _as_str(_dget(_dget(major, "none", {}), "tips", "")).strip()
        return _MajorParsed("", tips or DELETED_SOURCE_TIPS, [], "")

    # 未知 major：保留可读兜底文案（与 bison `UnknownMajor` 同义），不抛 KeyError
    logger.warning(f"动态 {dyn_id or '<无id>'} 含无法解析的 major 类型: {major_type or '<空>'}")
    return _MajorParsed("", desc_text or f"无法解析的动态，类型: {major_type}", [], "", degraded=True)


def _one_pic(obj: Any, key: str) -> list[str]:
    """取单张封面，空值不入列表（避免渲染层拿到空 url）"""
    url = _as_str(_dget(obj, key, "")).strip()
    return [url] if url else []


def _pic_urls_from_str_list(raw: Any) -> list[str]:
    """专栏 covers 是纯字符串数组"""
    if not isinstance(raw, (list, tuple)):
        return []
    return [url for url in (_as_str(cover).strip() for cover in raw) if url]


def _rich_text(nodes: Any) -> str:
    """富文本节点兜底拼接（summary.text 缺失时用）"""
    if not isinstance(nodes, (list, tuple)):
        return ""
    return "".join(_as_str(_dget(node, "text", "")) for node in nodes)


def _parse_live_rcmd(live_rcmd: Any, desc_text: str) -> _MajorParsed:
    """直播推荐卡：live_rcmd.content 是 JSON 文本，取 live_play_info"""
    raw = _as_str(_dget(live_rcmd, "content", "")).strip()
    if not raw:
        return _MajorParsed("", desc_text, [], "", degraded=True)
    try:
        content_obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"直播推荐卡 content 不是合法 JSON，降级为纯文本: {e!r}")
        return _MajorParsed("", desc_text, [], "", degraded=True)
    info = _dget(content_obj, "live_play_info", {})
    parent_area, area = _as_str(_dget(info, "parent_area_name", "")), _as_str(_dget(info, "area_name", ""))
    return _MajorParsed(
        _as_str(_dget(info, "title", "")),
        f"{parent_area} {area}".strip(),
        _one_pic(info, "cover"),
        _normalize_url(_dget(info, "link"), drop_query=True),
    )


# ---------------------------------------------------------------- 单条动态


def _build_dynamic(item: dict, dyn_id: str, depth: int) -> ParsedDynamic:
    """把一条 item 组装成 ParsedDynamic（内部用，调用方保证 item 是 dict）"""
    dyn_type = _as_str(_dget(item, "type", ""))
    category = CATEGORY_MAP.get(dyn_type, 0)
    degraded = category == 0
    if degraded:
        logger.warning(f"动态 {dyn_id or '<无id>'} 类型未知，按通用兜底处理: {dyn_type or '<空>'}")

    modules = _dget(item, "modules", {})
    author = _dget(modules, "module_author", {})
    # 置顶识别（设计文档 §4.2）：modules.module_tag.text == "置顶"
    is_pinned = _as_str(_dget(_dget(modules, "module_tag", {}), "text", "")).strip() == "置顶"

    major = _parse_major(item, dyn_id)
    degraded = degraded or major.degraded

    repost: Optional[ParsedDynamic] = None
    is_deleted_source = False
    if dyn_type == TYPE_FORWARD:
        repost, repost_degraded = _parse_repost(item, dyn_id, depth)
        degraded = degraded or repost_degraded
        is_deleted_source = bool(repost and repost.is_deleted_source)

    return ParsedDynamic(
        dyn_id=dyn_id,
        uid=_as_str(_dget(author, "mid", "")),
        category=category,
        dyn_type=dyn_type,
        is_pinned=is_pinned,
        pub_ts=_as_int(_dget(author, "pub_ts", 0)),
        nickname=_as_str(_dget(author, "name", "")),
        title=major.title,
        content=major.content,
        pics=list(major.pics),
        url=f"https://t.bilibili.com/{dyn_id}" if dyn_id else "",
        repost=repost,
        is_deleted_source=is_deleted_source,
        parse_degraded=degraded,
        major_url=major.url,
    )


def _parse_repost(item: dict, dyn_id: str, depth: int) -> tuple[Optional[ParsedDynamic], bool]:
    """解析转发动态的源动态；返回 (repost, 是否降级)。已删除源返回占位对象，绝不抛异常"""
    orig = _dget(item, "orig")
    if not isinstance(orig, dict):
        logger.warning(f"转发动态 {dyn_id} 没有原动态（orig 缺失）")
        return None, True
    if depth >= _MAX_REPOST_DEPTH:
        logger.warning(f"转发动态 {dyn_id} 的 orig 嵌套过深，忽略更深层级")
        return None, True

    orig_type = _as_str(_dget(orig, "type", ""))
    orig_id = _as_str(_dget(orig, "id_str", "")).strip()
    orig_modules = _dget(orig, "modules", {})
    orig_major = _dget(_dget(orig_modules, "module_dynamic", {}), "major", {})
    # 已删除的源动态：type 为 DYNAMIC_TYPE_NONE / major 为 MAJOR_TYPE_NONE / 干脆缺 modules
    is_deleted = (
        orig_type == TYPE_NONE
        or _as_str(_dget(orig_major, "type", "")) == "MAJOR_TYPE_NONE"
        or not orig_modules
    )
    if is_deleted:
        logger.debug(f"转发动态 {dyn_id} 的源动态已被删除")
        author = _dget(orig_modules, "module_author", {})
        # 占位对象：渲染层据此显示"源动态已被删除"；链接/配图留空，避免推出空的"转发详情"
        return (
            ParsedDynamic(
                dyn_id=orig_id,
                uid=_as_str(_dget(author, "mid", "")),
                dyn_type=orig_type or TYPE_NONE,
                pub_ts=_as_int(_dget(author, "pub_ts", 0)),
                nickname=_as_str(_dget(author, "name", "")),
                content=_parse_major(orig, orig_id).content or DELETED_SOURCE_TIPS,
                is_deleted_source=True,
            ),
            False,
        )

    parsed = _build_dynamic(orig, orig_id, depth + 1)
    return parsed, parsed.parse_degraded


# ---------------------------------------------------------------- 对外接口


def parse_item(item: Any) -> Optional[ParsedDynamic]:
    """解析单条动态；`id_str` 缺失返回 None（无法去重，跳过该条）"""
    if not isinstance(item, dict):
        logger.debug(f"动态条目不是 dict（{type(item).__name__}），跳过")
        return None
    dyn_id = _as_str(_dget(item, "id_str", "")).strip()
    if not dyn_id:
        logger.debug("动态缺少 id_str，跳过该条")
        return None
    try:
        return _build_dynamic(item, dyn_id, depth=0)
    except Exception as e:
        # 兜底：任何未预期异常都降级为「只有链接的空动态」，宁可丑不可漏
        logger.exception(f"解析动态 {dyn_id} 失败，降级为占位内容: {e!r}")
        return ParsedDynamic(
            dyn_id=dyn_id,
            dyn_type=_as_str(_dget(item, "type", "")),
            url=f"https://t.bilibili.com/{dyn_id}",
            parse_degraded=True,
        )


def should_skip(parsed: ParsedDynamic) -> bool:
    """是否跳过推送（设计文档 §4.2：跳过但调用方仍要推进 seen/游标）"""
    return parsed.dyn_type in SKIP_TYPES


def _sort_key(parsed: ParsedDynamic) -> tuple[int, int]:
    """按 int(dyn_id) 升序（雪花 id 单调递增）；非纯数字 id 排在最后且保持原相对顺序"""
    try:
        return (0, int(parsed.dyn_id))
    except (TypeError, ValueError):
        return (1, 0)


def parse_feed(data: Any) -> list[ParsedDynamic]:
    """解析 feed/space 的 data 段（也容忍直接传整个响应体），按动态 id 升序返回"""
    if not isinstance(data, dict):
        logger.warning(f"feed 响应不是 dict（{type(data).__name__}），本轮按空列表处理")
        return []
    container = data if "items" in data else _dget(data, "data", {})
    items = _dget(container, "items")
    if items is None:
        # items 为 null / 缺失是「该 UP 暂无动态」的正常形态，只 debug，避免刷屏
        logger.debug("feed 响应没有 items 字段，按空列表处理")
        return []
    if not isinstance(items, list):
        logger.warning(f"feed 响应 items 不是列表（{type(items).__name__}），本轮按空列表处理")
        return []

    parsed_list = [parsed for parsed in (parse_item(item) for item in items) if parsed is not None]
    parsed_list.sort(key=_sort_key)
    return parsed_list
