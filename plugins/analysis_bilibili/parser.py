"""按链接类型取信息，整理成卡片模板要的数据：视频、番剧、直播、专栏、动态"""

import re
from dataclasses import dataclass
from datetime import datetime
from time import localtime, strftime

from plugins.analysis_bilibili.api import get_json, resolve_short_link
from utils.bilibili import wbi_query

SHORT_DOMAINS = r"(?:b23\.tv|bili(?:22|23|33|2233)\.cn)"
DOMAINS = rf"(?:bilibili\.com|{SHORT_DOMAINS})"
_SHORT_LINK = re.compile(rf"{SHORT_DOMAINS}/\w+", re.I)

VIEW_API = "https://api.bilibili.com/x/web-interface/wbi/view"
SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
MEDIA_API = "https://api.bilibili.com/pgc/review/user"
LIVE_API = "https://api.live.bilibili.com/room/v1/Room/get_info"
LIVE_MASTER_API = "https://api.live.bilibili.com/live_user/v1/Master/info"
ARTICLE_API = "https://api.bilibili.com/x/article/viewinfo"
DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"


@dataclass
class Card:
    # video / bangumi / live / article / dynamic，对应模板里的一段
    kind: str
    # 随图附在下面的链接，PC 端打不开小程序卡片
    url: str
    data: dict


def _num(n: int) -> str:
    """B 站的写法：1.2万、4.4亿"""
    for unit, size in (("亿", 10**8), ("万", 10**4)):
        if n >= size:
            return f"{n / size:.1f}".removesuffix(".0") + unit
    return str(n)


def _paragraphs(text: str) -> str:
    """卡片里空行很占地方，段落之间只留换行"""
    return re.sub(r"\n\s*\n", "\n", text.replace("\r", "")).strip()


def _clock(seconds: int) -> str:
    h, rest = divmod(seconds, 3600)
    return (
        f"{h}:{rest // 60:02}:{rest % 60:02}"
        if h
        else f"{rest // 60:02}:{rest % 60:02}"
    )


async def _video(params: dict, page: int | None, t: int | None) -> Card:
    res = await get_json(f"{VIEW_API}?{await wbi_query(params)}")
    query = []
    part = ""
    duration = res["duration"]
    # 分享链接总带 ?p=1，单 P 视频的分 P 标题是上传时的文件名，不显示
    if page and res["videos"] > 1 and page <= len(res["pages"]):
        query.append(f"p={page}")
        chosen = res["pages"][page - 1]
        part = chosen["part"] if chosen["part"] != res["title"] else ""
        duration = chosen["duration"]
    else:
        page = None
    if t:
        query.append(f"t={t}")
    url = f"https://www.bilibili.com/video/{res['bvid']}"
    if query:
        url += "?" + "&".join(query)
    stat = res["stat"]
    return Card(
        "video",
        url,
        {
            "title": res["title"],
            "part": part,
            "page": page,
            "pages": res["videos"],
            "cover": res["pic"],
            "duration": _clock(duration),
            "start": _clock(t) if t else "",
            "progress": min(t / duration, 1) * 100 if t and duration else 0,
            "up": res["owner"]["name"],
            "face": res["owner"]["face"],
            "date": strftime("%Y-%m-%d", localtime(res["pubdate"])),
            "stats": {
                key: _num(stat[key])
                for key in ("view", "danmaku", "like", "coin", "favorite", "reply")
            },
            # 不写简介时接口给 "-"
            "desc": _paragraphs(res["desc"]) if res["desc"].strip() != "-" else "",
        },
    )


async def _bangumi(kind: str, key: str, t: int | None) -> Card:
    if kind == "md":
        media = await get_json(MEDIA_API, media_id=key)
        res = await get_json(SEASON_API, season_id=media["media"]["season_id"])
        url = f"https://www.bilibili.com/bangumi/media/md{key}"
    elif kind == "ss":
        res = await get_json(SEASON_API, season_id=key)
        url = f"https://www.bilibili.com/bangumi/play/ss{key}"
    else:
        res = await get_json(SEASON_API, ep_id=key)
        url = f"https://www.bilibili.com/bangumi/play/ep{key}"
    if t:
        url += f"?t={t}"
    episode = ""
    if kind == "ep":
        # PV、花絮这类不在正片列表里，找不到就不写分集标题
        found = next((e for e in res["episodes"] if str(e["ep_id"]) == key), None)
        if found:
            # title 多是集数，也可能是 PV1、SP 这类
            number = found["title"]
            number = f"第{number}话" if number.isdigit() else number
            episode = f"{number}  {found['long_title']}".strip()
    # 新番开播初期还没有评分
    rating = res.get("rating") or {}
    return Card(
        "bangumi",
        url,
        {
            "title": res["title"],
            "episode": episode,
            "cover": res["cover"],
            "score": rating.get("score"),
            "raters": _num(rating["count"]) if rating else "",
            "progress": res["new_ep"]["desc"],
            "styles": res["styles"],
            "views": _num(res["stat"]["views"]),
            "follows": _num(res["stat"]["favorites"]),
            "evaluate": _paragraphs(res["evaluate"]),
        },
    )


def _since(start: str) -> str:
    """开播时间 → 「已播 1小时23分」；接口未开播时给 0000-00-00 00:00:00"""
    minutes = int(
        (datetime.now() - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds()
        // 60
    )
    hours, minutes = divmod(minutes, 60)
    return f"已播 {hours}小时{minutes}分" if hours else f"已播 {minutes}分钟"


async def _live(room_id: str) -> Card:
    room = await get_json(LIVE_API, room_id=room_id)
    master = (await get_json(LIVE_MASTER_API, uid=room["uid"]))["info"]
    status = room["live_status"]
    return Card(
        "live",
        f"https://live.bilibili.com/{room['room_id']}",
        {
            "title": room["title"],
            "cover": room["user_cover"] or room["keyframe"],
            # 0 未开播 1 直播中 2 轮播中
            "status": status,
            "since": _since(room["live_time"]) if status == 1 else "",
            "anchor": master["uname"],
            "face": master["face"],
            "area": [room["parent_area_name"], room["area_name"]],
            "online": _num(room["online"]),
            "tags": [tag for tag in room["tags"].split(",") if tag],
        },
    )


async def _article(cvid: str) -> Card:
    res = await get_json(ARTICLE_API, id=cvid, mobi_app="pc", **{"from": "web"})
    stats = res["stats"]
    return Card(
        "article",
        f"https://www.bilibili.com/read/cv{cvid}",
        {
            "title": res["title"],
            "banner": res["banner_url"] or next(iter(res["origin_image_urls"]), ""),
            "author": res["author_name"],
            "stats": {
                key: _num(stats[key])
                for key in ("view", "like", "favorite", "coin", "reply")
            },
        },
    )


def _dynamic_body(item: dict) -> dict:
    """一条动态的作者、正文、配图、附带的视频和商品；转发时原动态也用它整理"""
    author = item["modules"].get("module_author") or {}
    module = item["modules"]["module_dynamic"]
    major = module.get("major") or {}
    # 不带 itemOpusStyle 时图文动态的正文为空（desc 是 null）
    opus = major.get("opus") or {}
    text = (module.get("desc") or opus.get("summary") or {}).get("text", "")
    if major.get("type") == "MAJOR_TYPE_NONE":
        text = major["none"]["tips"]  # 原动态已删除
    images = [pic["url"] for pic in opus.get("pics", [])]
    images += [pic["src"] for pic in (major.get("draw") or {}).get("items", [])]
    archive = major.get("archive")
    additional = module.get("additional") or {}
    goods = []
    if additional.get("type") == "ADDITIONAL_TYPE_GOODS":
        goods = [(g["name"], g["price"]) for g in additional["goods"]["items"]]
    return {
        "name": author.get("name", ""),
        "face": author.get("face", ""),
        "time": author.get("pub_time", ""),
        "title": opus.get("title") or "",
        "text": _paragraphs(text),
        "images": images,
        "archive": archive
        and {
            "title": archive["title"],
            "cover": archive["cover"],
            "duration": archive["duration_text"],
        },
        "goods": goods,
    }


async def _dynamic(dynamic_id: str) -> Card:
    detail = await get_json(DYNAMIC_API, id=dynamic_id, features="itemOpusStyle")
    item = detail["item"]
    body = _dynamic_body(item)
    if item["type"] == "DYNAMIC_TYPE_FORWARD":
        body["orig"] = _dynamic_body(item["orig"])
    return Card("dynamic", f"https://t.bilibili.com/{item['id_str']}", body)


async def parse(link: str) -> Card | None:
    if short := _SHORT_LINK.search(link):
        link = await resolve_short_link(short[0])
    page = re.search(r"[?&]p=(\d+)", link)
    page = int(page[1]) if page else None
    t = re.search(r"[?&]t=(\d+)", link)
    t = int(t[1]) if t else None

    if m := re.search(r"BV[0-9A-Za-z]{10}", link):
        return await _video({"bvid": m[0]}, page, t)
    if m := re.search(r"(?:^|/)av(\d+)", link):
        return await _video({"aid": m[1]}, page, t)
    if m := re.search(r"bangumi/(?:play|media)/(ep|ss|md)(\d+)", link):
        return await _bangumi(m[1], m[2], t)
    if m := re.search(r"live\.bilibili\.com/(?:blanc/|h5/)?(\d+)", link):
        return await _live(m[1])
    if m := re.search(r"(?:/read/(?:cv|mobile|native)(?:/|\?id=)?|^cv)(\d+)", link):
        return await _article(m[1])
    if m := re.search(r"(?:[tm]\.bilibili\.com/(?:opus/)?|/opus/)(\d+)", link):
        return await _dynamic(m[1])
    return None
