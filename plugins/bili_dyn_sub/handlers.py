"""B 站动态订阅命令：b站订阅 / b站退订 / b站订阅列表 / b站订阅测试，均限超管。

私聊用来在不打扰生产群的前提下验证插件：会改订阅的命令私聊时必须给群号。
推送目标只由订阅关系决定，不接入插件开关——订阅关系就是唯一开关，不想收就退订，
否则会出现“订阅列表里有、就是不推”的状态。
"""

from __future__ import annotations

import re

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.exception import ActionFailed, NetworkError
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from utils.logging import get_exc_desc
from utils.text import truncate
from plugins.bili_dyn_sub import api
from plugins.bili_dyn_sub.config import plugin_config
from plugins.bili_dyn_sub.credential import credential_manager
from plugins.bili_dyn_sub.parser import CATEGORY_NAMES, ParsedDynamic, parse_feed
from plugins.bili_dyn_sub.render import build_messages, build_text
from plugins.bili_dyn_sub.scheduler import SendTarget, backoff, dispatch_segments
from plugins.bili_dyn_sub.store import store

_UID_URL_RE = re.compile(r"space\.bilibili\.com/(\d{1,20})")
_UID_PREFIX_RE = re.compile(r"^uid\s*[:：]\s*(\d{1,20})$", re.IGNORECASE)
_UID_PLAIN_RE = re.compile(r"^(\d{1,20})$")
_GROUP_ID_RE = re.compile(r"^[1-9]\d{0,14}$")

# 「b站订阅测试」纯文本模式的预览条数与单条长度
_PREVIEW_MAX_ITEMS = 2
_PREVIEW_TEXT_LENGTH = 300
_TEXT_MODE_KEYWORDS = frozenset({"text", "txt", "文本", "纯文本"})


def parse_uid(raw: str) -> str | None:
    """纯数字 / `UID:xxx` / `https://space.bilibili.com/xxx`"""
    for pattern in (_UID_URL_RE, _UID_PREFIX_RE, _UID_PLAIN_RE):
        match = pattern.search(raw.strip())
        if match:
            # 去掉前导零，与 state.json 里的 key 一致
            return match.group(1).lstrip("0") or None
    return None


def _parse_target(event: MessageEvent, args: Message) -> tuple[str | None, int | None]:
    """`<UID> [群号]`：省略群号时取当前群，私聊没有当前群；解析不了的部分返回 None"""
    parts = args.extract_plain_text().split()
    uid = parse_uid(parts[0]) if parts else None
    if len(parts) > 1:
        group_id = int(parts[1]) if _GROUP_ID_RE.match(parts[1]) else None
    else:
        group_id = getattr(event, "group_id", None)
    return uid, group_id


def _usage(command: str) -> str:
    return (
        f"用法：{command} <UID> [群号]\n"
        f"例如：{command} 13148307\n"
        f"　　　{command} https://space.bilibili.com/13148307 819157441\n"
        "群聊里省略群号表示当前群；私聊必须给群号"
    )


async def _membership_warning(bot: Bot, group_id: int) -> str:
    """群号是手打的，打错一位照样能订上，此后每轮推送都失败——回执要说清楚"""
    try:
        groups = await bot.get_group_list()
    except (ActionFailed, NetworkError) as e:
        logger.warning(
            f"查询群列表失败，跳过群 {group_id} 的归属提示: {get_exc_desc(e)}"
        )
        return ""
    if any(group["group_id"] == group_id for group in groups):
        return ""
    return f"\n⚠️ 机器人当前不在群 {group_id} 中，订阅已记录但推送会失败，请确认群号是否输错"


bili_sub = on_command(
    "b站订阅",
    aliases={"B站订阅", "b站动态订阅"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
bili_unsub = on_command(
    "b站退订",
    aliases={"B站退订", "b站取消订阅"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
bili_list = on_command(
    "b站订阅列表",
    aliases={"B站订阅列表"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)
bili_test = on_command(
    "b站订阅测试",
    aliases={"B站订阅测试"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


@bili_sub.handle()
async def handle_subscribe(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    uid, group_id = _parse_target(event, args)
    if uid is None or group_id is None:
        await bili_sub.finish(_usage("b站订阅"))
    if group_id in store.groups(uid):
        await bili_sub.finish(f"群 {group_id} 已订阅 UID {uid}（{store.name(uid)}）")

    name = await api.fetch_user_name(uid)
    if not name:
        await bili_sub.finish(
            f"无法获取 UID {uid} 的昵称，可能是 UID 不存在或 B 站暂时不可访问，请确认后重试"
        )
    # 作用于当前群时 bot 显然在群里，只有跨群才查
    note = (
        ""
        if group_id == getattr(event, "group_id", None)
        else await _membership_warning(bot, group_id)
    )
    need_baseline = not store.has_baseline(uid)
    await store.add_subscription(uid, name, group_id)
    logger.info(f"群 {group_id} 订阅 UID {uid}（{name}）")

    tip = (
        f"✅ 已为群 {group_id} 订阅：{name}\n"
        f"UID {uid}\n"
        f"轮询间隔约 {plugin_config.poll_interval_seconds}s"
    )
    if need_baseline:
        tip += "\n首轮只建立基线、不回推历史动态，之后的新动态才会推送"
    await bili_sub.finish(tip + note)


@bili_unsub.handle()
async def handle_unsubscribe(event: MessageEvent, args: Message = CommandArg()):
    uid, group_id = _parse_target(event, args)
    if uid is None or group_id is None:
        await bili_unsub.finish(_usage("b站退订"))
    name = store.name(uid)
    if not await store.remove_subscription(uid, group_id):
        await bili_unsub.finish(f"群 {group_id} 未订阅 UID {uid}")
    logger.info(f"群 {group_id} 退订 UID {uid}")
    await bili_unsub.finish(f"✅ 已为群 {group_id} 退订：{name}\nUID {uid}")


@bili_list.handle()
async def handle_list(event: MessageEvent):
    """群聊列当前群的订阅；私聊列全局，并标出每条推到哪些群"""
    group_id = getattr(event, "group_id", None)
    scope = f"群 {group_id} " if group_id else "全局 "
    items = store.subscriptions(group_id)

    if items:
        lines = [f"📋 {scope}B 站动态订阅（{len(items)} 个）："]
    else:
        lines = [f"{scope}暂无 B 站动态订阅"]
    lines.append(credential_manager.describe())
    remaining = backoff.remaining()
    if remaining:
        lines.append(f"   ⚠️ 取数退避中，剩余 {remaining}s")
    for uid, name, groups in items:
        line = f"• {name or '（未知昵称）'}\n  UID {uid} | 上次成功 {store.last_success(uid) or '从未成功'}"
        if group_id is None:
            line += f"\n  推送群：{'、'.join(str(g) for g in groups)}"
        lines.append(line)
    if not items:
        lines.append(f"使用「b站订阅 <UID>{'' if group_id else ' <群号>'}」添加")
    await bili_list.finish("\n".join(lines))


@bili_test.handle()
async def handle_test(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """立即拉一次并回显，不推送、不改状态。默认按真实推送流程渲染并发出最新一条，
    预览和推送走同一条发送路径，否则“预览没问题”证明不了“推到群里没问题”。"""
    parts = args.extract_plain_text().split()
    uid = parse_uid(parts[0]) if parts else None
    if not uid:
        await bili_test.finish(
            "请提供要测试的 UP 主 UID，例如：\n"
            "b站订阅测试 13148307        （完整渲染，与真实推送一致）\n"
            "b站订阅测试 13148307 text   （纯文本，只看解析结果）"
        )
    text_only = len(parts) > 1 and parts[1].lower() in _TEXT_MODE_KEYWORDS
    await bili_test.send(f"正在拉取 UID {uid} 的动态...")

    try:
        data = await api.fetch_space_feed(uid)
    except api.BiliApiError as e:
        logger.warning(f"订阅测试 UID {uid} 取数失败: {e}")
        await bili_test.finish(f"❌ 取数失败：{e}\n{credential_manager.describe()}")

    parsed_list = parse_feed(data)
    header = [
        f"✅ 取数成功：UID {uid} 共 {len(parsed_list)} 条动态",
        credential_manager.describe(),
        f"基线：{'已建立' if store.has_baseline(uid) else '未建立'}",
    ]
    if not parsed_list:
        header.append("（该 UP 当前没有可解析的动态）")
        await bili_test.finish("\n".join(header))

    if text_only:
        header.append("—— 模式：纯文本（解析预览）")
        for parsed in reversed(parsed_list[-_PREVIEW_MAX_ITEMS:]):
            header.append(_preview_head(uid, parsed))
            header.append(truncate(build_text(parsed), _PREVIEW_TEXT_LENGTH))
        await bili_test.finish("\n".join(header))

    latest = parsed_list[-1]
    header.append("—— 模式：完整渲染（与真实推送逐条一致）")
    header.append(_preview_head(uid, latest))
    header.append("正在渲染，请稍候...")
    await bili_test.send("\n".join(header))

    msgs = await build_messages(latest)
    target = (
        SendTarget(group_id=event.group_id)
        if isinstance(event, GroupMessageEvent)
        else SendTarget(user_id=event.user_id)
    )
    await dispatch_segments(bot, target, msgs)

    tail = f"以上为卡片 + {len(msgs.pics)} 张配图的实际推送效果"
    if msgs.pages:
        tail += f"，专栏全文 {len(msgs.pages)} 页"
    if msgs.card[0].type == "text":
        tail += "\n⚠️ 文字卡片渲染失败，已降级为纯文本（真实推送同此行为，请检查浏览器/字体）"
    await bili_test.finish(tail)


def _preview_head(uid: str, parsed: ParsedDynamic) -> str:
    """一行动态摘要：id | 分类 | 特殊标记 | 是否已推送"""
    flags = []
    if parsed.is_pinned:
        flags.append("置顶")
    if parsed.is_deleted_source:
        flags.append("源动态已删除")
    if parsed.parse_degraded:
        flags.append("解析降级")
    return (
        f"—— {parsed.dyn_id} | {CATEGORY_NAMES.get(parsed.category, parsed.dyn_type or '未知')}"
        f"{' | ' + '/'.join(flags) if flags else ''}"
        f"{' | 已推送' if store.is_seen(uid, parsed.dyn_id) else ''}"
    )
