"""cs_stats 的全部命令。

平台（绑定的单位）：pw 完美（绑的是 SteamID，完美天梯和官匹共用）、5e。
模式（查询的单位）：pvp 完美天梯、mm 官匹、5e。
账号用 #序号 选，和“最近第 N 局”的数字区分开。
"""

import asyncio
import json
from dataclasses import dataclass

import httpx
from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import GROUP, Bot, GroupMessageEvent, Message
from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State

from core.access import is_superuser, switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown
from core.settings import reload_plugin_config
from plugins.cs_stats import fivee_api, pw_api, render, store
from plugins.cs_stats.config import PLUGIN_ID, QUERY_ID, ROAST_ID, plugin_config
from plugins.cs_stats.roast import generate
from utils.logging import get_exc_desc
from utils.onebot.media import image_segment
from utils.onebot.messages import mentioned_users
from utils.onebot.rules import command_boundary

# 第一个是主命令词，其余是别名
MODE_WORDS = {"pvp": ("完美",), "mm": ("官匹",), "5e": ("5e", "5E")}
PLATFORM_WORDS = {"pw": ("完美", "官匹"), "5e": ("5e", "5E")}
MODE_PLATFORM = {"pvp": "pw", "mm": "pw", "5e": "5e"}
MAX_CANDIDATES = 5
# 完美对局列表一页最多拉这么多，最近第 N 局的 N 不超过它
MAX_NTH = 20


class QueryError(Exception):
    """参数、绑定或数据不足，文案直接回给用户"""


def _command(words: tuple[str, ...], suffix: str, feature: str):
    name, *aliases = (word + suffix for word in words)
    return on_command(
        name,
        aliases=set(aliases),
        rule=command_boundary & switch_rule(feature),
        permission=GROUP,
        priority=5,
        block=True,
    )


def _account_index(token: str) -> int | None:
    """「#2」→ 1；不是 #序号 返回 None。是否越界由调用方按绑定数判断"""
    if token.startswith("#") and token[1:].isdecimal() and int(token[1:]) >= 1:
        return int(token[1:]) - 1
    return None


@dataclass
class QueryArgs:
    target: str
    is_self: bool
    index: int
    nth: int
    refresh: bool


def parse_args(
    bot: Bot, event: GroupMessageEvent, arg: Message, *, allow_nth: bool, allow_refresh: bool
) -> QueryArgs:
    index, nth, refresh = 0, 1, False
    for token in arg.extract_plain_text().split():
        if (account_index := _account_index(token)) is not None:
            index = account_index
        elif allow_refresh and token in ("-r", "刷新"):
            refresh = True
        elif allow_nth and token.isdecimal() and 1 <= int(token) <= MAX_NTH:
            nth = int(token)
        else:
            raise QueryError(f"看不懂参数「{token}」")
    # @bot 和回复 bot 的消息都不算指定了对象
    mentioned = [qq for qq in mentioned_users(arg) if str(qq) != bot.self_id]
    reply_user = event.reply and event.reply.sender.user_id
    if mentioned:
        target = str(mentioned[0])
    elif reply_user and str(reply_user) != bot.self_id:
        target = str(reply_user)
    else:
        target = str(event.user_id)
    return QueryArgs(target, target == str(event.user_id), index, nth, refresh)


def pick_account(args: QueryArgs, platform: str) -> dict:
    word = PLATFORM_WORDS[platform][0]
    accounts = store.accounts(args.target, platform)
    if not accounts:
        if args.is_self:
            raise QueryError(f"你还没有绑定{word}账号，发「{word}绑定 昵称」绑定")
        raise QueryError(f"对方还没有绑定{word}账号")
    if args.index >= len(accounts):
        raise QueryError(f"只绑定了 {len(accounts)} 个{word}账号")
    return accounts[args.index]


async def _notify_token_invalid(bot: Bot) -> None:
    logger.warning("[cs_stats] 完美登录态失效，完美查询已暂停")
    text = (
        "cs_stats：完美登录态失效，完美查询已暂停。\n"
        "在完美 App 登录页收验证码后运行 uv run python scripts/pw_login.py，再发「完美重载」。"
    )
    for qq in get_driver().config.superusers:
        try:
            await bot.send_private_msg(user_id=int(qq), message=text)
        except Exception as exc:
            logger.warning(f"[cs_stats] 通知 {qq} 失败: {get_exc_desc(exc)}")


async def _fetch(matcher: type[Matcher], bot: Bot, coro):
    """外部接口出错时直接结束命令"""
    try:
        return await coro
    except QueryError as exc:
        await matcher.finish(str(exc))
    except pw_api.PWTokenInvalid as exc:
        if exc.newly:
            await _notify_token_invalid(bot)
        await matcher.finish(str(exc))
    except (pw_api.PWError, fivee_api.FiveEError) as exc:
        logger.warning(f"[cs_stats] 查询失败: {exc}")
        await matcher.finish(str(exc))
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        logger.warning(f"[cs_stats] 接口请求失败: {get_exc_desc(exc)}")
        await matcher.finish("平台接口暂时连不上，稍后再试")


async def _render(matcher: type[Matcher], coro) -> bytes:
    try:
        return await coro
    except Exception:
        logger.exception("[cs_stats] 图片渲染失败")
        await matcher.finish("图片生成失败")


# ============== 数据加载：战绩、锐评、SU 查询共用 ==============


async def load_profile(mode: str, account: dict) -> tuple[dict, str]:
    """返回（生涯 view, 生涯锐评的水位）"""
    if mode == "5e":
        career, recent, arena = await asyncio.gather(
            fivee_api.career(account["uuid"]), fivee_api.matches(account["uuid"]), fivee_api.arena(account["id"])
        )
        view = render.fivee_profile_view(career, recent, arena, account)
        latest = recent[0]["match_id"] if recent else ""
        return view, f"{career.get('match_total')}:{latest}"
    stats_call = pw_api.pvp_stats if mode == "pvp" else pw_api.official_stats
    stats, (public, recent), avatar = await asyncio.gather(
        stats_call(account["id"]), pw_api.match_list(account["id"], mode, size=5), pw_api.steam_avatar(account["id"])
    )
    if not public:
        raise QueryError("对方没有公开完美战绩")
    latest = (stats.get("historyDates") or [""])[0]
    if mode == "pvp":
        if not int(stats.get("cnt") or 0):
            raise QueryError(f"近 {plugin_config.pvp_season_lookback + 1} 个赛季都没有完美天梯记录")
        return render.pvp_profile_view(stats, recent, account, avatar), f"{stats['seasonId']}:{stats['cnt']}:{latest}"
    if not int(stats.get("cnt") or 0):
        raise QueryError("完美上没有官匹记录")
    return render.mm_profile_view(stats, recent, account, avatar), f"{stats['cnt']}:{latest}"


async def load_match(mode: str, account: dict, nth: int) -> dict:
    if mode == "5e":
        items = await fivee_api.matches(account["uuid"])
        if nth > len(items):
            raise QueryError(f"5E 只能查到最近 {len(items)} 局")
        detail = await fivee_api.match_detail(items[nth - 1]["match_id"])
        return render.fivee_match_view(detail, account["uuid"])
    public, items = await pw_api.match_list(account["id"], mode, size=nth)
    if not public:
        raise QueryError("对方没有公开完美战绩")
    if nth > len(items):
        raise QueryError(f"最近只有 {len(items)} 局{MODE_WORDS[mode][0]}记录")
    detail = await pw_api.match_detail(str(items[nth - 1]["matchId"]), mode)
    return render.pw_match_view(detail, account["id"], mode)


def _roast_key(mode: str, account: dict) -> str:
    return f"5e:{account['uuid']}" if mode == "5e" else f"pw:{account['id']}:{mode}"


async def _check_query_cd(matcher: type[Matcher], event: GroupMessageEvent) -> None:
    remaining = cooldown_remaining(QUERY_ID, event)
    if remaining:
        await matcher.finish(f"查询冷却中，还剩 {remaining} 秒")


async def _roast(
    matcher: type[Matcher], kind: str, key: str, watermark: str | None, view: dict, refresh: bool, event
) -> dict | None:
    """存储里有就直接用；要生成时才检查和扣锐评 CD。返回锐评或 None（生成失败）"""
    stored = store.get_roast(kind, key)
    if stored and (watermark is None or stored["watermark"] == watermark) and not refresh:
        return stored
    remaining = cooldown_remaining(ROAST_ID, event)
    if remaining:
        await matcher.finish(f"锐评冷却中，还剩 {remaining} 秒")
    await matcher.send("锐评生成中…")
    roast = await generate(kind, view)
    if roast:
        store.save_roast(kind, key, {**roast, "watermark": watermark} if watermark else roast)
        mark_cooldown(ROAST_ID, event)
    return roast


# ============== 查询命令 ==============


def _register_queries(mode: str) -> None:
    words = MODE_WORDS[mode]
    platform = MODE_PLATFORM[mode]

    profile = _command(words, "战绩", QUERY_ID)
    single = _command(words, "单局", QUERY_ID)
    career_roast = _command(words, "锐评", ROAST_ID)
    match_roast = _command(words, "单局锐评", ROAST_ID)

    async def _args(matcher, bot, event, arg, *, allow_nth, allow_refresh):
        try:
            args = parse_args(bot, event, arg, allow_nth=allow_nth, allow_refresh=allow_refresh)
            account = pick_account(args, platform)
        except QueryError as exc:
            await matcher.finish(str(exc))
        if args.refresh and not is_superuser(str(event.user_id)):
            await matcher.finish("只有 bot 主能强制重新生成锐评")
        return args, account

    @profile.handle()
    async def _(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
        args, account = await _args(profile, bot, event, arg, allow_nth=False, allow_refresh=False)
        await _check_query_cd(profile, event)
        view, _watermark = await _fetch(profile, bot, load_profile(mode, account))
        image = await _render(profile, render.render_profile(view))
        await profile.send(image_segment(image))
        mark_cooldown(QUERY_ID, event)

    @single.handle()
    async def _(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
        args, account = await _args(single, bot, event, arg, allow_nth=True, allow_refresh=False)
        await _check_query_cd(single, event)
        view = await _fetch(single, bot, load_match(mode, account, args.nth))
        image = await _render(single, render.render_match(view))
        await single.send(image_segment(image))
        mark_cooldown(QUERY_ID, event)

    @career_roast.handle()
    async def _(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
        args, account = await _args(career_roast, bot, event, arg, allow_nth=False, allow_refresh=True)
        view, watermark = await _fetch(career_roast, bot, load_profile(mode, account))
        key = _roast_key(mode, account)
        roast = await _roast(career_roast, "career", key, watermark, view, args.refresh, event)
        image = await _render(career_roast, render.render_profile(view, roast))
        message = image_segment(image) + ("" if roast else "锐评生成失败，先看数据吧")
        await career_roast.send(message)

    @match_roast.handle()
    async def _(bot: Bot, event: GroupMessageEvent, arg: Message = CommandArg()):
        args, account = await _args(match_roast, bot, event, arg, allow_nth=True, allow_refresh=True)
        view = await _fetch(match_roast, bot, load_match(mode, account, args.nth))
        key = f"{_roast_key(mode, account)}:{view['match_id']}"
        roast = await _roast(match_roast, "match", key, None, view, args.refresh, event)
        image = await _render(match_roast, render.render_match(view, roast))
        message = image_segment(image) + ("" if roast else "锐评生成失败，先看数据吧")
        await match_roast.send(message)


for _mode in MODE_WORDS:
    _register_queries(_mode)


# ============== 绑定与账号管理 ==============


async def search_candidates(platform: str, keyword: str) -> list[dict]:
    """搜索结果补齐卡片要显示的分数；5E 顺便把 uuid 转好，绑定时存下"""
    if platform == "pw":
        found = (await pw_api.search(keyword))[:MAX_CANDIDATES]
        avatars = await asyncio.gather(*(pw_api.steam_avatar(c["id"]) for c in found))
        return [
            {**c, "avatar": avatar, "score_label": "本赛季天梯分", "score": c["score"] or "-"}
            for c, avatar in zip(found, avatars)
        ]
    found = (await fivee_api.search(keyword))[:MAX_CANDIDATES]
    uuids = await asyncio.gather(*(fivee_api.uuid_of(c["id"]) for c in found))
    careers = await asyncio.gather(*(fivee_api.career(uuid) for uuid in uuids))
    return [
        {
            **c,
            "uuid": uuid,
            "score_label": "ELO",
            "score": round(float(career.get("elo_9") or 0)) or "-",
            "extra": f"生涯 {career.get('match_total') or 0} 场",
        }
        for c, uuid, career in zip(found, uuids, careers)
    ]


def _choose(candidates: list[dict], reply: str) -> dict | None:
    if len(candidates) == 1 and reply == "确认":
        return candidates[0]
    if reply.isdecimal() and 1 <= int(reply) <= len(candidates):
        return candidates[int(reply) - 1]
    return None


def _choose_hint(candidates: list[dict]) -> str:
    action = "回复「确认」" if len(candidates) == 1 else f"回复序号 1～{len(candidates)}"
    return f"{action}选择，回复其他内容取消"


def _account_line(platform: str, index: int, account: dict) -> str:
    tag = f"SteamID …{account['id'][-4:]}" if platform == "pw" else f"5E ID {account['id']}"
    return f"#{index + 1} {account['nickname']}（{tag}）" + ("　主号" if index == 0 else "")


def _register_accounts(platform: str) -> None:
    words = PLATFORM_WORDS[platform]
    word = words[0]

    bind = _command(words, "绑定", PLUGIN_ID)
    unbind = _command(words, "解绑", PLUGIN_ID)
    listing = _command(words, "账号", PLUGIN_ID)
    main = _command(words, "主号", PLUGIN_ID)
    id_hint = "SteamID64" if platform == "pw" else "5E 个人主页 ID"

    @bind.handle()
    async def _(bot: Bot, event: GroupMessageEvent, state: T_State, arg: Message = CommandArg()):
        keyword = arg.extract_plain_text().strip()
        if not keyword:
            await bind.finish(f"用法：{word}绑定 <昵称或{id_hint}>")
        qq = str(event.user_id)
        bound = store.accounts(qq, platform)
        if len(bound) >= plugin_config.max_accounts_per_platform:
            await bind.finish(
                f"每个人最多绑定 {plugin_config.max_accounts_per_platform} 个{word}账号，先用「{word}解绑 #序号」解绑一个"
            )
        found = await _fetch(bind, bot, search_candidates(platform, keyword))
        bound_ids = {a["id"] for a in bound}
        candidates = [c for c in found if c["id"] not in bound_ids]
        if not candidates:
            await bind.finish("搜到的号你都已经绑定了" if found else f"没有搜到{word}玩家「{keyword}」")
        state["candidates"] = candidates
        image = await _render(bind, render.render_candidates(platform, candidates, "绑定"))
        await bind.send(image_segment(image) + _choose_hint(candidates))

    @bind.got("choice")
    async def _(event: GroupMessageEvent, state: T_State, choice: str = ArgPlainText()):
        picked = _choose(state["candidates"], choice.strip())
        if picked is None:
            await bind.finish("已取消绑定")
        qq = str(event.user_id)
        # 完美头像每次现取 Steam 的，不存；5E 生涯接口不带头像，绑定时存下
        keys = ("id", "nickname") if platform == "pw" else ("id", "nickname", "avatar", "uuid")
        account = {k: picked[k] for k in keys}
        store.add_account(qq, platform, account)
        count = len(store.accounts(qq, platform))
        await bind.finish(f"已绑定{word}账号 {picked['nickname']}（#{count}{'，主号' if count == 1 else ''}）")

    @unbind.handle()
    async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
        index = _account_index(arg.extract_plain_text().strip() or "#1")
        qq = str(event.user_id)
        bound = store.accounts(qq, platform)
        if index is None or index >= len(bound):
            await unbind.finish(f"用法：{word}解绑 [#序号]，发「{word}账号」查看序号" if bound else f"你没有绑定{word}账号")
        removed = store.remove_account(qq, platform, index)
        rest = store.accounts(qq, platform)
        tail = f"，现在的主号是 {rest[0]['nickname']}" if rest and index == 0 else ""
        await unbind.finish(f"已解绑 {removed['nickname']}{tail}")

    @listing.handle()
    async def _(event: GroupMessageEvent):
        bound = store.accounts(str(event.user_id), platform)
        if not bound:
            await listing.finish(f"你还没有绑定{word}账号，发「{word}绑定 昵称」绑定")
        lines = [_account_line(platform, i, a) for i, a in enumerate(bound)]
        await listing.finish(f"你绑定的{word}账号：\n" + "\n".join(lines))

    @main.handle()
    async def _(event: GroupMessageEvent, arg: Message = CommandArg()):
        index = _account_index(arg.extract_plain_text().strip())
        qq = str(event.user_id)
        bound = store.accounts(qq, platform)
        if index is None or index >= len(bound):
            await main.finish(f"用法：{word}主号 #序号，发「{word}账号」查看序号")
        store.set_main(qq, platform, index)
        await main.finish(f"主号已设为 {store.accounts(qq, platform)[0]['nickname']}")


for _platform in PLATFORM_WORDS:
    _register_accounts(_platform)


# ============== SU 命令 ==============

su_query = on_command("cs查询", rule=command_boundary, permission=SUPERUSER, priority=5, block=True)
SU_MODES = {word: mode for mode, words in MODE_WORDS.items() for word in words}


async def _su_show(bot: Bot, mode: str, account: dict) -> None:
    view, _watermark = await _fetch(su_query, bot, load_profile(mode, account))
    image = await _render(su_query, render.render_profile(view))
    await su_query.finish(image_segment(image))


@su_query.handle()
async def _(bot: Bot, state: T_State, arg: Message = CommandArg()):
    parts = arg.extract_plain_text().split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in SU_MODES:
        await su_query.finish("用法：cs查询 <完美|官匹|5e> <昵称或ID>")
    mode = SU_MODES[parts[0]]
    candidates = await _fetch(su_query, bot, search_candidates(MODE_PLATFORM[mode], parts[1].strip()))
    if not candidates:
        await su_query.finish("没有搜到")
    if len(candidates) == 1:
        await _su_show(bot, mode, candidates[0])
    state["mode"], state["candidates"] = mode, candidates
    image = await _render(su_query, render.render_candidates(MODE_PLATFORM[mode], candidates, "查询"))
    await su_query.send(image_segment(image) + _choose_hint(candidates))


@su_query.got("choice")
async def _(bot: Bot, state: T_State, choice: str = ArgPlainText()):
    picked = _choose(state["candidates"], choice.strip())
    if picked is None:
        await su_query.finish("已取消")
    await _su_show(bot, state["mode"], picked)


pw_reload = on_command("完美重载", rule=command_boundary, permission=SUPERUSER, priority=5, block=True)


@pw_reload.handle()
async def _():
    reload_plugin_config(PLUGIN_ID, plugin_config)
    pw_api.reset_token_state()
    await pw_reload.finish("已重新读取 cs_stats 配置，完美查询恢复")
