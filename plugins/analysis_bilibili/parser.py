"""按链接类型取信息、拼回复：视频、番剧、直播、专栏、动态"""

import re
from dataclasses import dataclass, field
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
class Reply:
    text: str
    # 封面、专栏图、动态图；一期只发文字，留给二期的 template
    images: list[str] = field(default_factory=list)


def _num(n: int) -> str:
    return f"{n / 10000:.2f}万" if n > 10000 else str(n)


def _date(ts: int) -> str:
    return strftime("%Y-%m-%d %H:%M:%S", localtime(ts))


async def _video(params: dict, page: int | None, t: str | None) -> Reply:
    res = await get_json(f"{VIEW_API}?{await wbi_query(params)}")
    url = f"https://www.bilibili.com/video/av{res['aid']}"
    query = []
    lines = [f"标题：{res['title']}"]
    # 分享链接总带 ?p=1，单 P 视频的分 P 标题是上传时的文件名，不显示
    if page and res["videos"] > 1 and page <= len(res["pages"]):
        query.append(f"p={page}")
        part = res["pages"][page - 1]["part"]
        if part != res["title"]:
            lines.append(f"小标题：{part}")
    if t:
        query.append(f"t={t}")
    if query:
        url += "?" + "&".join(query)
    stat = res["stat"]
    lines += [
        f"UP：{res['owner']['name']} | 日期：{_date(res['pubdate'])}",
        f"播放：{_num(stat['view'])} | 弹幕：{_num(stat['danmaku'])} | 收藏：{_num(stat['favorite'])}",
        f"点赞：{_num(stat['like'])} | 硬币：{_num(stat['coin'])} | 评论：{_num(stat['reply'])}",
    ]
    desc = [line for line in f"简介：{res['desc']}".split("\n") if line]
    lines.append("\n".join(desc[:3]) + "……" if len(desc) > 3 else "\n".join(desc))
    return Reply("\n".join([url, *lines]), [res["pic"]])


async def _bangumi(kind: str, key: str, t: str | None) -> Reply:
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
    lines = [url, f"番剧：{res['title']}"]
    if kind == "ep":
        # PV、花絮这类不在正片列表里，找不到就不写分集标题
        episode = next((e for e in res["episodes"] if str(e["ep_id"]) == key), None)
        if episode:
            lines.append(f"标题：{episode['long_title']}")
    lines += [
        res["new_ep"]["desc"],
        f"类型：{','.join(res['styles'])}",
        f"简介：{res['evaluate']}",
    ]
    return Reply("\n".join(lines), [res["cover"]])


async def _live(room_id: str) -> Reply:
    room = await get_json(LIVE_API, room_id=room_id)
    master = await get_json(LIVE_MASTER_API, uid=room["uid"])
    status = {1: "直播中", 2: "轮播中"}.get(room["live_status"], "未开播")
    lines = [
        f"https://live.bilibili.com/{room['room_id']}",
        f"[{status}]标题：{room['title']}",
        f"主播：{master['info']['uname']}  当前分区：{room['parent_area_name']}-{room['area_name']}",
        f"人气：{_num(room['online'])}",
    ]
    if room["tags"]:
        lines.append(f"标签：{room['tags']}")
    if room["live_status"]:
        lines.append(
            "独立播放器：https://www.bilibili.com/blackboard/live/live-activity-player.html"
            f"?enterTheRoom=0&cid={room['room_id']}"
        )
    return Reply("\n".join(lines), [room["user_cover"]] if room["user_cover"] else [])


async def _article(cvid: str) -> Reply:
    res = await get_json(ARTICLE_API, id=cvid, mobi_app="pc", **{"from": "web"})
    stats = res["stats"]
    lines = [
        f"标题：{res['title']}",
        f"作者：{res['author_name']} (https://space.bilibili.com/{res['mid']})",
        f"阅读数：{_num(stats['view'])} 收藏数：{_num(stats['favorite'])} 硬币数：{_num(stats['coin'])}",
        f"分享数：{_num(stats['share'])} 点赞数：{_num(stats['like'])} 不喜欢数：{_num(stats['dislike'])}",
        f"https://www.bilibili.com/read/cv{cvid}",
    ]
    return Reply("\n".join(lines), res["origin_image_urls"])


async def _dynamic(dynamic_id: str) -> Reply:
    # 不带 itemOpusStyle 时图文动态的正文为空（desc 是 null）
    detail = await get_json(DYNAMIC_API, id=dynamic_id, features="itemOpusStyle")
    item = detail["item"]
    module = item["modules"]["module_dynamic"]
    major = module["major"] or {}
    opus = major.get("opus") or {}
    text = (module["desc"] or opus.get("summary") or {}).get("text", "")
    images = [pic["url"] for pic in opus.get("pics", [])]
    images += [pic["src"] for pic in (major.get("draw") or {}).get("items", [])]

    lines = [text.replace("\r", "\n").replace("\n\n", "\n").strip()] if text else []
    if images:
        lines.append(f"PS：动态中包含{len(images)}张图片")
    attached = []
    if archive := major.get("archive"):
        attached += [
            f"转发视频：https:{archive['jump_url']}",
            f"简介：{archive['desc']}",
        ]
        images.append(archive["cover"])
    if item["type"] == "DYNAMIC_TYPE_FORWARD":
        attached.append(f"转发动态：https://t.bilibili.com/{item['orig']['id_str']}")
    additional = module["additional"] or {}
    if additional.get("type") == "ADDITIONAL_TYPE_GOODS":
        attached += [
            f"{g['name']}（{g['price']}）" for g in additional["goods"]["items"]
        ]
    if attached:
        lines += ["-" * 40, *attached]
    lines.append(f"\n动态链接：https://t.bilibili.com/{item['id_str']}")
    return Reply("\n".join(lines), images)


async def parse(link: str) -> Reply | None:
    if short := _SHORT_LINK.search(link):
        link = await resolve_short_link(short[0])
    page = re.search(r"[?&]p=(\d+)", link)
    page = int(page[1]) if page else None
    t = re.search(r"[?&]t=(\d+)", link)
    t = t[1] if t else None

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
