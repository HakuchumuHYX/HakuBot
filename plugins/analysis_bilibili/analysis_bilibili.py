import json
import re
import nonebot

from time import localtime, strftime
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse
from aiohttp import ClientSession

from plugins.analysis_bilibili.ExpiringCache import ExpiringCache
from plugins.analysis_bilibili.sign import get_query, get_ticket

# analysis_stat : {group_id: ExpiringCache}
analysis_stat: Dict[int, ExpiringCache] = {}


config = nonebot.get_driver().config
analysis_display_image = getattr(config, "analysis_display_image", False)
analysis_display_image_list = getattr(config, "analysis_display_image_list", [])
images_size = getattr(config, "analysis_images_size", "")
cover_images_size = getattr(config, "analysis_cover_images_size", "")
reanalysis_time = getattr(config, "analysis_reanalysis_time", 0)

# 旧 /x/web-interface/view 在无 Referer 时会被 412 成 HTML，aiohttp.json() 即 ContentTypeError。
# 当前可用接口是带 wbi 的 view（未签名在本机也可，签名更稳）。
VIEW_API = "https://api.bilibili.com/x/web-interface/wbi/view"
LIVE_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"
LIVE_MASTER_API = "https://api.live.bilibili.com/live_user/v1/Master/info"

_SHARE_URL_KEYS = ("qqdocurl", "jumpUrl", "jump_url", "qqdocUrl")
_BILI_HOST_MARKERS = (
    "b23.tv",
    "bilibili.com",
    "bili2233.cn",
    "bili22.cn",
    "bili23.cn",
    "bili33.cn",
)


def resize_image(src: str, is_cover=False) -> str:
    img_type = src[-3:]
    if cover_images_size and is_cover:
        return f"{src}@{cover_images_size}.{img_type}"
    if images_size:
        return f"{src}@{images_size}.{img_type}"
    return src


def _is_bili_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in _BILI_HOST_MARKERS)


def _find_share_url(obj: Any) -> str:
    """从 QQ 卡片 JSON 里找出 bilibili 相关链接（qqdocurl / jumpUrl）。"""
    if isinstance(obj, dict):
        for key in _SHARE_URL_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and _is_bili_url(value):
                return value
        url = obj.get("url")
        if isinstance(url, str) and _is_bili_url(url):
            return url
        for value in obj.values():
            found = _find_share_url(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_share_url(item)
            if found:
                return found
    return ""


def extract_share_url(text: str) -> str:
    """从 QQ 小程序 / 图文分享 JSON 中提取 B 站链接。"""
    if not text:
        return ""
    raw = (
        text.replace("\\/", "/")
        .replace("&amp;", "&")
        .replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
    )
    candidates = []
    stripped = raw.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        found = _find_share_url(data)
        if found:
            return found
    short = re.search(r"https?://(?:b23\.tv|bili(?:22|23|33|2233)\.cn)/\w+", raw, re.I)
    if short:
        return short.group(0)
    return ""


def _query_params(url: str) -> dict:
    qs = parse_qs(urlparse(url).query)
    params = {}
    for key in ("bvid", "aid"):
        if qs.get(key):
            params[key] = qs[key][0]
    return params


async def bili_get_json(session: ClientSession, url: str, **kwargs) -> dict:
    """GET 并解析 JSON。B 站风控页是 text/html，不能走 resp.json()。"""
    async with session.get(url, **kwargs) as resp:
        status = resp.status
        body = await resp.text()
    if status == 412:
        raise RuntimeError("HTTP 412")
    if status >= 500:
        raise RuntimeError(f"HTTP {status}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"HTTP {status} 非 JSON") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"HTTP {status} JSON 不是对象")
    return data


async def _ensure_ticket(session: ClientSession) -> None:
    try:
        ticket = await get_ticket()
        if ticket:
            session.cookie_jar.update_cookies({"bili_ticket": ticket})
    except Exception as e:
        nonebot.logger.debug(f"analysis_bilibili get_ticket failed: {e!r}")


async def _signed_view_url(params: dict) -> str:
    try:
        query = await get_query(dict(params))
        return f"{VIEW_API}?{query}"
    except Exception as e:
        nonebot.logger.debug(
            f"analysis_bilibili wbi 签名失败，改用未签名 wbi/view: {e!r}"
        )
        return f"{VIEW_API}?{urlencode(params)}"


async def bili_keyword(
    group_id: Optional[int], text: str, session: ClientSession
) -> Union[List[Union[List[str], str]], str, bool]:
    try:
        await _ensure_ticket(session)
        share_url = extract_share_url(text)
        if share_url:
            text = share_url
        if re.search(r"(b23.tv)|(bili(22|23|33|2233).cn)", text, re.I):
            text = await b23_extract(text, session)

        # 提取url
        url, page, time_location = extract(text)
        # 如果是小程序就去搜索标题
        if not url:
            if title := re.search(r'"desc":("[^"哔哩]+")', text):
                vurl = await search_bili_by_title(title[1], session)
                if vurl:
                    url, page, time_location = extract(vurl)

        if not url:
            return False

        # 获取视频详细信息
        msg, vurl = "", ""
        if "view?" in url:
            msg, vurl = await video_detail(
                url, page=page, time_location=time_location, session=session
            )
        elif "pgc" in url:
            msg, vurl = await bangumi_detail(url, time_location, session)
        elif "xlive" in url or "get_info?" in url or "/Room/" in url:
            msg, vurl = await live_detail(url, session)
        elif "article" in url:
            msg, vurl = await article_detail(url, page, session)
        elif "dynamic" in url:
            msg, vurl = await dynamic_detail(url, session)

        # 避免多个机器人解析重复推送
        if group_id and vurl:
            if group_id in analysis_stat:
                if analysis_stat[group_id].get(vurl):
                    return False
                analysis_stat[group_id].set(vurl)
            else:
                analysis_stat[group_id] = ExpiringCache(expire_seconds=reanalysis_time)
                analysis_stat[group_id].set(vurl)

    except Exception as e:
        msg = "bili_keyword Error: {}".format(type(e))
    return msg


async def b23_extract(text: str, session: ClientSession) -> str:
    b23 = re.compile(r"b23.tv/(\w+)|(bili(22|23|33|2233).cn)/(\w+)").search(
        text.replace("\\", "")
    )
    if not b23:
        # 正文只含裸域名（无路径）时正则不命中，直接返回原文交给 extract() 处理
        return text
    url = f"https://{b23[0]}"

    async with session.get(url) as resp:
        return str(resp.url)


def extract(text: str) -> Tuple[str, Optional[str], Optional[str]]:
    try:
        url = ""
        # 视频分p
        page = re.compile(r"([?&]|&amp;)p=\d+").search(text)
        # 视频播放定位时间
        time = re.compile(r"([?&]|&amp;)t=\d+").search(text)
        # 主站视频 av 号
        aid = re.compile(r"av\d+").search(text)
        # 主站视频 bv 号
        bvid = re.compile(r"BV([A-Za-z0-9]{10})+").search(text)
        # 番剧视频页
        epid = re.compile(r"ep\d+").search(text)
        # 番剧剧集ssid(season_id)
        ssid = re.compile(r"ss\d+").search(text)
        # 番剧详细页
        mdid = re.compile(r"md\d+").search(text)
        # 直播间
        room_id = re.compile(r"live.bilibili.com/(blanc/|h5/)?(\d+)").search(text)
        # 文章
        cvid = re.compile(r"(/read/(cv|mobile|native)(/|\?id=)?|^cv)(\d+)").search(text)
        # 动态 (opus 新格式)
        opus_id = re.compile(r"bilibili.com/opus/(\d+)", re.I).search(text)
        # 动态
        dynamic_id_type2 = re.compile(
            r"(t|m).bilibili.com/(\d+)\?(.*?)(&|&amp;)type=2"
        ).search(text)
        # 动态
        dynamic_id = re.compile(r"(t|m).bilibili.com/(opus/)?(\d+)").search(text)
        if bvid:
            url = f"{VIEW_API}?bvid={bvid[0]}"
        elif aid:
            url = f"{VIEW_API}?aid={aid[0][2:]}"
        elif epid:
            url = f"https://api.bilibili.com/pgc/view/web/season?ep_id={epid[0][2:]}"
        elif ssid:
            url = (
                f"https://api.bilibili.com/pgc/view/web/season?season_id={ssid[0][2:]}"
            )
        elif mdid:
            url = f"https://api.bilibili.com/pgc/review/user?media_id={mdid[0][2:]}"
        elif room_id:
            url = f"{LIVE_INFO_API}?room_id={room_id[2]}"
        elif cvid:
            page = cvid[4]
            url = f"https://api.bilibili.com/x/article/viewinfo?id={page}&mobi_app=pc&from=web"
        elif opus_id:
            url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id={opus_id[1]}"
        elif dynamic_id_type2:
            url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?rid={dynamic_id_type2[2]}&type=2"
        elif dynamic_id:
            url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id={dynamic_id[3]}"
        return url, page, time
    except Exception:
        return "", None, None


async def search_bili_by_title(title: str, session: ClientSession) -> str:
    # set headers
    mainsite_url = "https://www.bilibili.com"
    async with session.get(mainsite_url) as resp:
        assert resp.status == 200

    query = await get_query({"keyword": title})
    search_url = f"https://api.bilibili.com/x/web-interface/wbi/search/all/v2?{query}"

    bili_ticket = await get_ticket()

    session.cookie_jar.update_cookies({"bili_ticket": bili_ticket})

    result = await bili_get_json(session, search_url)

    if result.get("code") == -412:
        nonebot.logger.warning(f"analysis_bilibili: {result}")
        return

    data = result.get("data") or {}
    for i in data.get("result") or []:
        if i.get("result_type") != "video":
            continue
        items = i.get("data") or i.get("result") or []
        if items:
            return items[0].get("arcurl")


# 处理超过一万的数字
def handle_num(num: int) -> str:
    if num > 10000:
        num = f"{num / 10000:.2f}万"
    return num


async def video_detail(
    url: str, session: ClientSession, **kwargs
) -> Tuple[List[str], str]:
    try:
        params = _query_params(url)
        if not params:
            return "无法识别视频 ID", url
        api_url = await _signed_view_url(params)
        payload = await bili_get_json(session, api_url)
        res = payload.get("data")
        if isinstance(res, dict) and res.get("v_voucher") and not res.get("aid"):
            return "B站风控校验中，请稍后再试", url
        if payload.get("code") not in (0, None) or not res:
            return "解析到视频被删了/稿件不可见或审核中/权限不足", url
        vurl = f"https://www.bilibili.com/video/av{res['aid']}"
        title = f"\n标题：{res['title']}\n"

        has_image = False
        if analysis_display_image or "video" in analysis_display_image_list:
            has_image = True

        cover = resize_image(res["pic"]) if has_image else ""
        vurl = "\n" + vurl if cover else vurl
        if page := kwargs.get("page"):
            page = page[0].replace("&amp;", "&")
            p = int(page[3:])
            if p <= len(res["pages"]):
                vurl += f"?p={p}"
                part = res["pages"][p - 1]["part"]
                if part != res["title"]:
                    title += f"小标题：{part}\n"
        if time_location := kwargs.get("time_location"):
            time_location = time_location[0].replace("&amp;", "&")[3:]
            if page:
                vurl += f"&t={time_location}"
            else:
                vurl += f"?t={time_location}"
        pubdate = strftime("%Y-%m-%d %H:%M:%S", localtime(res["pubdate"]))
        tname = f"类型：{res['tname']} | UP：{res['owner']['name']} | 日期：{pubdate}\n"
        stat = f"播放：{handle_num(res['stat']['view'])} | 弹幕：{handle_num(res['stat']['danmaku'])} | 收藏：{handle_num(res['stat']['favorite'])}\n"
        stat += f"点赞：{handle_num(res['stat']['like'])} | 硬币：{handle_num(res['stat']['coin'])} | 评论：{handle_num(res['stat']['reply'])}\n"
        desc = f"简介：{res['desc']}"
        desc_list = desc.split("\n")
        desc = "".join(i + "\n" for i in desc_list if i)
        desc_list = desc.split("\n")
        if len(desc_list) > 4:
            desc = desc_list[0] + "\n" + desc_list[1] + "\n" + desc_list[2] + "……"
        msg = [cover, vurl, title, tname, stat, desc]
        return msg, vurl
    except Exception as e:
        nonebot.logger.warning(f"视频解析出错: {type(e).__name__}: {e}")
        msg = "视频解析出错，B站接口可能暂时不可用"
        return msg, None


async def bangumi_detail(
    url: str, time_location: str, session: ClientSession
) -> Tuple[List[str], str]:
    try:
        is_media = False
        if "media_id" in url:
            is_media = True
            media = await bili_get_json(session, url)
            ssid = ((media.get("result") or {}).get("media") or {}).get("season_id")
            if not ssid:
                return "番剧信息获取失败", url
            url = f"https://api.bilibili.com/pgc/view/web/season?season_id={ssid}"

        payload = await bili_get_json(session, url)
        res = payload.get("result") or payload.get("data")
        if not res:
            return "番剧信息获取失败", url

        has_image = False
        if analysis_display_image or "bangumi" in analysis_display_image_list:
            has_image = True

        cover = resize_image(res["cover"], is_cover=True) if has_image else ""
        title = f"番剧：{res['title']}\n"
        desc = f"{res['new_ep']['desc']}\n"
        long_title = ""
        styles = "".join(f"{i}," for i in res["styles"])
        styles = f"类型：{styles[:-1]}\n"
        evaluate = f"简介：{res['evaluate']}\n"
        if is_media:
            vurl = f"https://www.bilibili.com/bangumi/media/md{res['media_id']}"
        elif "season_id" in url:
            vurl = f"https://www.bilibili.com/bangumi/play/ss{res['season_id']}"
        else:
            epid = re.compile(r"ep_id=\d+").search(url)[0][len("ep_id=") :]
            for i in res["episodes"]:
                if str(i["ep_id"]) == epid:
                    long_title = f"标题：{i['long_title']}\n"
                    break
            vurl = f"https://www.bilibili.com/bangumi/play/ep{epid}"
        if time_location:
            time_location = time_location[0].replace("&amp;", "&")[3:]
            vurl += f"?t={time_location}"
        vurl = "\n" + vurl if cover else vurl
        msg = [cover, f"{vurl}\n", title, long_title, desc, styles, evaluate]
        return msg, vurl
    except Exception as e:
        nonebot.logger.warning(f"番剧解析出错: {type(e).__name__}: {e}")
        msg = "番剧解析出错，B站接口可能暂时不可用"
        return msg, None


def _live_room_id_from_url(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    if qs.get("room_id"):
        return str(qs["room_id"][0])
    match = re.search(r"room_id=(\d+)", url)
    return match.group(1) if match else ""


async def _fetch_live_payload(url: str, session: ClientSession) -> dict:
    """优先旧 xlive 接口，-352/失败时回落到 room/get_info。"""
    room_id = _live_room_id_from_url(url)
    payload = {}
    try:
        payload = await bili_get_json(session, url)
    except Exception as e:
        nonebot.logger.debug(f"analysis_bilibili live primary failed: {e!r}")
    data = payload.get("data") if payload.get("code") == 0 else None
    if isinstance(data, dict) and (data.get("room_info") or data.get("room_id")):
        return data
    if room_id:
        fallback = await bili_get_json(session, f"{LIVE_INFO_API}?room_id={room_id}")
        if fallback.get("code") == 0 and isinstance(fallback.get("data"), dict):
            return fallback["data"]
    return {}


async def _live_uname(session: ClientSession, uid: Any, default: str = "") -> str:
    if not uid:
        return default
    try:
        master = await bili_get_json(session, f"{LIVE_MASTER_API}?uid={uid}")
        uname = ((master.get("data") or {}).get("info") or {}).get("uname")
        if uname:
            return uname
    except Exception as e:
        nonebot.logger.debug(f"analysis_bilibili live uname failed: {e!r}")
    return default or f"UID {uid}"


async def live_detail(url: str, session: ClientSession) -> Tuple[List[str], str]:
    try:
        res = await _fetch_live_payload(url, session)
        if not res:
            return "直播间信息获取失败", None

        if "room_info" in res:
            room = res["room_info"]
            uname = ((res.get("anchor_info") or {}).get("base_info") or {}).get(
                "uname"
            ) or await _live_uname(session, room.get("uid"))
            cover_src = room.get("cover") or ""
            watched = ((res.get("watched_show") or {}).get("text_large")) or ""
            lock_status = room.get("lock_status")
            lock_time = room.get("lock_time")
        else:
            room = res
            uname = await _live_uname(session, room.get("uid"))
            cover_src = room.get("user_cover") or room.get("cover") or ""
            watched = ""
            lock_status = room.get("lock_status")
            lock_time = room.get("lock_time")

        room_id = room.get("room_id")
        title = room.get("title") or ""
        live_status = room.get("live_status")
        parent_area_name = room.get("parent_area_name") or ""
        area_name = room.get("area_name") or ""
        online = room.get("online") or 0
        tags = room.get("tags") or ""

        has_image = False
        if analysis_display_image or "live" in analysis_display_image_list:
            has_image = True

        cover = (
            resize_image(cover_src, is_cover=True) if has_image and cover_src else ""
        )
        vurl = f"https://live.bilibili.com/{room_id}\n"
        if lock_status:
            if lock_time:
                lock_time = strftime("%Y-%m-%d %H:%M:%S", localtime(lock_time))
                title = f"[已封禁]直播间封禁至：{lock_time}\n"
            else:
                title = f"[已封禁]标题：{title}\n"
        elif live_status == 1:
            title = f"[直播中]标题：{title}\n"
        elif live_status == 2:
            title = f"[轮播中]标题：{title}\n"
        else:
            title = f"[未开播]标题：{title}\n"
        up = f"主播：{uname}  当前分区：{parent_area_name}-{area_name}\n"
        watch = (
            f"观看：{watched}  直播时的人气上一次刷新值：{handle_num(online)}\n"
            if watched
            else f"人气：{handle_num(online)}\n"
        )
        if tags:
            tags = f"标签：{tags}\n"
        if live_status:
            player = f"独立播放器：https://www.bilibili.com/blackboard/live/live-activity-player.html?enterTheRoom=0&cid={room_id}"
        else:
            player = ""
        vurl = "\n" + vurl if cover else vurl
        msg = [cover, vurl, title, up, watch, tags, player]
        return msg, vurl
    except Exception as e:
        nonebot.logger.warning(f"直播间解析出错: {type(e).__name__}: {e}")
        msg = "直播间解析出错，B站接口可能暂时不可用"
        return msg, None


async def article_detail(
    url: str, cvid: str, session: ClientSession
) -> Tuple[List[Union[List[str], str]], str]:
    try:
        payload = await bili_get_json(session, url)
        res = payload.get("data")
        if not res:
            return "专栏信息获取失败", url

        has_image = False
        if analysis_display_image or "article" in analysis_display_image_list:
            has_image = True

        images = (
            [resize_image(i) for i in res["origin_image_urls"]] if has_image else []
        )
        vurl = f"https://www.bilibili.com/read/cv{cvid}"
        title = f"标题：{res['title']}\n"
        up = f"作者：{res['author_name']} (https://space.bilibili.com/{res['mid']})\n"
        view = f"阅读数：{handle_num(res['stats']['view'])} "
        favorite = f"收藏数：{handle_num(res['stats']['favorite'])} "
        coin = f"硬币数：{handle_num(res['stats']['coin'])}"
        share = f"分享数：{handle_num(res['stats']['share'])} "
        like = f"点赞数：{handle_num(res['stats']['like'])} "
        dislike = f"不喜欢数：{handle_num(res['stats']['dislike'])}"
        desc = view + favorite + coin + "\n" + share + like + dislike + "\n"
        msg = [images, title, up, desc, vurl]
        return msg, vurl
    except Exception as e:
        nonebot.logger.warning(f"专栏解析出错: {type(e).__name__}: {e}")
        msg = "专栏解析出错，B站接口可能暂时不可用"
        return msg, None


async def dynamic_detail(
    url: str, session: ClientSession
) -> Tuple[List[Union[List[str], str]], str]:
    try:
        payload = await bili_get_json(session, url)
        if payload.get("code") != 0:
            return "动态信息获取失败（可能被风控）", None
        res = (payload.get("data") or {}).get("item")
        if not res:
            return "动态内容为空", None
        dynamic_id = res["id_str"]
        vurl = f"https://t.bilibili.com/{dynamic_id}\n"

        # 动态内容
        module_dynamic = res["modules"]["module_dynamic"]
        module_type = res["type"]

        # 文字信息
        desc = module_dynamic["desc"] if module_dynamic["desc"] else {"text": ""}
        content = desc.get("text").replace("\r", "\n").replace("\n\n", "\n")

        has_image = False
        if analysis_display_image or "dynamic" in analysis_display_image_list:
            has_image = True

        # 额外信息(会员购)
        additional_msg = []
        additional = module_dynamic.get("additional")
        if isinstance(additional, dict):
            additional_type = additional.get("type")
            if additional_type == "ADDITIONAL_TYPE_GOODS":
                items = additional.get("goods", {}).get("items", [])
                for item in items:
                    additional_msg.append(
                        f"{item.get('name')}（{item.get('price')}）\n"
                    )

        # DRAW图片/ARCHIVE转发视频/null纯文字
        draws = []
        archive_cover = ""
        archive_msg = ""
        split = "\n----------------------------------------\n"
        major = module_dynamic["major"]
        if isinstance(major, dict):
            if module_type == "DYNAMIC_TYPE_DRAW":
                split = split if additional_msg else ""
                if has_image:
                    draws = [
                        resize_image(i.get("src"))
                        for i in major.get("draw").get("items", [])
                    ]
                else:
                    items_len = len(major.get("draw").get("items", []))
                    content += f"\nPS：动态中包含{items_len}张图片"

            elif module_type == "DYNAMIC_TYPE_AV":
                jump_url = major.get("archive").get("jump_url")
                archive_cover = (
                    resize_image(major.get("archive").get("cover")) if has_image else ""
                )
                archive_msg += f"转发视频：https:{jump_url}\n"
                archive_msg += f"简介：{major.get('archive').get('desc')}"

        elif module_type == "DYNAMIC_TYPE_FORWARD":
            desc = module_dynamic["desc"]
            orig_id = res.get("orig").get("id_str")
            archive_msg += f"转发动态：https://t.bilibili.com/{orig_id}\n"
        else:
            split = ""

        msg = [
            content,
            draws,
            split,
            archive_cover,
            archive_msg,
            additional_msg,
            f"\n动态链接：{vurl}",
        ]
        return msg, vurl
    except Exception as e:
        nonebot.logger.warning(f"动态解析出错: {type(e).__name__}: {e}")
        msg = "动态解析出错，B站接口可能暂时不可用"
        return msg, None
