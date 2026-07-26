"""
B 站动态订阅插件入口（自研，替换 nonebot-bison）

- 订阅命令：b站订阅 / b站退订 / b站订阅列表 / b站订阅测试（均限 SUPERUSER，私聊同样受限）
- 群聊 / 私聊双通道：**一个命令一个 matcher，按事件类型分流 handler**（本仓 buaa_msm 的范式；
  同名命令注册两个 matcher 会在启动时报 Duplicated prefix rule）。私聊通道的存在意义是
  「不打扰生产群的前提下验证插件」：只读命令私聊直接可用，会改状态的命令要求显式给出群号。
- 定时任务：由 `from . import scheduler` 的 import 副作用注册（见 scheduler.py）
- 推送目标：**完全由订阅关系（state.json 的 groups）决定**，不叠加任何群级开关；
  「不想收了」的正确操作是「b站退订」，而不是禁用插件（理由见 docs/bili_dyn_sub_design.md §11.4.3）
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from ..utils.tools import get_exc_desc, get_logger, truncate
from . import api
from .backoff import backoff_manager
from .config import Config, plugin_config
from .credential import credential_manager
from .parser import CATEGORY_NAMES, parse_feed
from .render import build_messages, build_text
from .store import store

# 导入 scheduler 以注册定时任务（import 即注册），并复用其发送分发逻辑
from . import scheduler as _scheduler  # noqa: F401
from .scheduler import SendTarget, dispatch_segments

logger = get_logger("bili_dyn_sub")

__plugin_meta__ = PluginMetadata(
    name="B站动态订阅",
    description="自研 B 站 UP 主动态订阅推送（持久化去重 + 推送窗口闸门，替换 nonebot-bison）",
    usage="""命令列表（均仅限超级用户；群聊与私聊都可用）：
- b站订阅 <UID / UID:xxx / 空间链接> [群号]
    群聊：省略群号即订阅到当前群；私聊：必须显式给出群号
- b站退订 <UID> [群号]
    群聊：省略群号即从当前群退订；私聊：必须显式给出群号
- b站订阅列表
    群聊：列出本群订阅；私聊：列出全局订阅（标注每条推送到哪些群）
- b站订阅测试 <UID> [text]
    群聊 / 私聊均可用；立即拉取一次（不改状态）。默认按**真实推送流程**渲染最新一条
    并发到当前会话（私聊即只发给自己），所见即所得；加 text 则只回显解析出的文本
""",
    type="application",
    homepage="",
    config=Config,
    supported_adapters={"~onebot.v11"},
)


# ---------------------------------------------------------------- 参数解析

_UID_URL_RE = re.compile(r"space\.bilibili\.com/(\d{1,20})")
_UID_PREFIX_RE = re.compile(r"^uid\s*[:：]\s*(\d{1,20})$", re.IGNORECASE)
_UID_PLAIN_RE = re.compile(r"^(\d{1,20})$")
# 群号只接受纯数字（QQ 群号长度不固定，只做上限保护，避免把明显的乱输入当群号用）
_GROUP_ID_RE = re.compile(r"^\d{1,15}$")

# 回显测试结果时最多展示的动态条数与单条文本长度（仅纯文本模式用）
_PREVIEW_MAX_ITEMS = 2
_PREVIEW_TEXT_LENGTH = 300
# 「b站订阅测试」切换到纯文本模式的第二参数
_TEXT_MODE_KEYWORDS = frozenset({"text", "txt", "文本", "纯文本"})

# 校验目标群归属时给 get_group_list 的超时（查不到就静默跳过，绝不拖死命令）
_GROUP_LIST_TIMEOUT = 8.0


def parse_uid(raw: str) -> Optional[str]:
    """从用户输入里解析 UID：纯数字 / `UID:xxx` / `https://space.bilibili.com/xxx`"""
    text = (raw or "").strip()
    if not text:
        return None
    for pattern in (_UID_URL_RE, _UID_PREFIX_RE, _UID_PLAIN_RE):
        match = pattern.search(text)
        if not match:
            continue
        # 去掉可能的前导零，保证与 state.json 里的 key 一致
        uid = match.group(1).lstrip("0")
        return uid or None
    return None


def parse_group_id(raw: str) -> Optional[int]:
    """解析群号：必须是正整数纯数字，否则返回 None（由调用方给出用法提示）。

    非数字 / 负号 / 小数点 / 超长（>15 位）一律拒绝；`0`（以及 `000`）也拒绝——
    不存在 0 号群，放行只会在 state.json 里留下一条永远推不出去的死订阅。
    """
    text = (raw or "").strip()
    if not _GROUP_ID_RE.match(text):
        return None
    group_id = int(text)
    return group_id if group_id > 0 else None


def split_command_args(raw: str) -> tuple[str, str]:
    """把 `<UID> [群号]` 拆成 (UID 原文, 群号原文)；缺失的部分为空串，多余的部分忽略。

    UID 允许是不含空格的空间链接，所以按空白切分即可。
    """
    parts = (raw or "").split()
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "")


def _uid_usage(command: str, *, private: bool, url_form: bool = False) -> str:
    """UID 缺失 / 无法解析时的用法提示。

    私聊版本必须带群号（没有"当前群"可回落）；url_form=True 时额外展示空间链接写法。
    """
    if private:
        lines = ["请提供 UP 主 UID 与目标群号，例如：", f"{command} 13148307 819157441"]
        if url_form:
            lines.append(f"{command} https://space.bilibili.com/13148307 819157441")
        return "\n".join(lines)

    lines = ["请提供 UP 主 UID，例如：", f"{command} 13148307"]
    if url_form:
        lines.append(f"{command} https://space.bilibili.com/13148307")
    lines.append(f"（如需作用于其他群，在末尾追加群号：{command} 13148307 819157441）")
    return "\n".join(lines)


def _group_id_usage(command: str, group_raw: str, *, private: bool) -> str:
    """群号缺失 / 非法时的用法提示。

    私聊没有"当前群"可回落，缺群号必须报错；群聊只有在显式给了非法群号时才会走到这里。
    """
    if group_raw:
        # 拒绝原因不止"有非数字"（还有 0、负数、超长），文案不能只提纯数字，否则输 0 的人会困惑
        head = f"群号「{group_raw}」不是有效的 QQ 群号（应为 1~15 位数字且大于 0），请检查后重试"
    else:
        head = f"私聊使用「{command}」必须显式指定目标群号（私聊没有当前群可以回落）"
    tail = f"用法：{command} <UID> <群号>\n例如：{command} 13148307 819157441"
    if not private:
        tail += f"\n省略群号则作用于当前群：{command} 13148307"
    return f"{head}\n{tail}"


# ---------------------------------------------------------------- 展示辅助


def _format_subscription(item: dict[str, Any], *, show_groups: bool = False) -> str:
    """订阅列表里的单行展示：UID + 昵称 + 上次成功时间 + 退避状态。

    show_groups=True 时额外标出推送到哪些群（私聊的全局列表用，群聊列表里是冗余信息）。
    """
    uid = str(item.get("uid", ""))
    name = str(item.get("name", "")) or "（未知昵称）"
    last_success = store.get_last_success(uid) or "从未成功"
    line = f"• {name}\n  UID {uid} | 上次成功 {last_success}"
    if show_groups:
        groups = item.get("groups") or []
        line += f"\n  推送群：{'、'.join(str(g) for g in groups) or '（无）'}"
    remaining = backoff_manager.remaining_seconds(uid)
    if remaining > 0:
        line += f"\n  ⚠️ 退避中，剩余 {remaining}s"
    categories = item.get("categories") or []
    if categories and len(categories) < len(CATEGORY_NAMES):
        names = "/".join(CATEGORY_NAMES.get(c, str(c)) for c in categories)
        line += f"\n  分类：{names}"
    return line


def _format_login_state() -> str:
    """当前登录态的一句话描述（只读内存里最近一次校验结论，不发任何请求）。

    取数接口判不出登录态（失效 sessdata 会被静默降级成匿名请求且照样返回 code=0），
    结论只来自 credential 对 nav 接口的周期校验；启动后首次校验前显示"未校验"。
    """
    status = credential_manager.get_login_status()
    if status is not None:
        return status.summary()
    if (plugin_config.sessdata or "").strip():
        return "已配置 sessdata，尚未校验（启动后约 30s 完成首次校验）"
    return "未配置 sessdata（匿名取数）"


def _format_global_status() -> str:
    """订阅列表开头的全局状态：cookie 来源 + 登录态，让人一眼看出是登录态还是匿名"""
    return f"🔑 {credential_manager.describe()}\n   登录态：{_format_login_state()}"


# ---------------------------------------------------------------- 业务动作
#
# 群聊与私聊只在「目标群从哪来」上有区别，真正的动作与文案共用下面三个函数；
# 回执一律写明作用对象（群号），避免私聊操作时搞不清改了哪个群。


async def membership_warning(bot: Bot, group_id: int) -> str:
    """目标群不在 bot 的群列表里时给出警告文案，否则返回空串。

    私聊（或群聊里跨群操作）时群号是手打的，打错一位照样能写进 state.json，
    此后每轮推送都会 ActionFailed，而回执却说"已订阅"——回执不能这么误导人。
    这里只做一次廉价的只读校验：**查不到就静默跳过**（超时/接口不支持/返回异常格式），
    绝不因为校验失败而挡下订阅，也绝不让命令卡在这一步。
    """
    try:
        groups = await asyncio.wait_for(bot.get_group_list(), timeout=_GROUP_LIST_TIMEOUT)
        joined = {int(g["group_id"]) for g in groups if isinstance(g, dict) and "group_id" in g}
    except Exception as e:
        logger.debug(f"校验群 {group_id} 归属失败，跳过归属提示: {get_exc_desc(e)}")
        return ""

    if not joined or group_id in joined:
        return ""
    return f"\n⚠️ 机器人当前不在群 {group_id} 中，订阅已记录但推送会失败，请确认群号是否输错"


async def do_subscribe(uid: str, group_id: int, *, note: str = "") -> str:
    """为指定群订阅该 UP 主，返回回执文案。

    note 是调用方附加的提示（目前只有 membership_warning 的群归属警告），
    只挂在"订阅确实生效/已存在"的分支后面——取昵称失败时本就没写状态，再提群归属只会添乱。
    """
    if group_id in store.get_groups(uid):
        return f"群 {group_id} 已订阅 UID {uid}（{store.get_name(uid) or '未知昵称'}）{note}"

    try:
        name = await api.fetch_user_name(uid)
    except Exception as e:
        # fetch_user_name 内部已兜底，这里只防御意外异常，避免命令直接崩
        logger.error(f"查询 UID {uid} 昵称失败: {get_exc_desc(e)}")
        name = None

    if not name:
        return f"无法获取 UID {uid} 的昵称，可能是 UID 不存在或 B 站暂时不可访问，请确认后重试"

    need_baseline = not store.is_baseline_initialized(uid)
    store.add_subscription(uid, name, group_id)
    logger.info(f"群 {group_id} 订阅 UID {uid}（{name}）")

    tip = (
        f"✅ 已为群 {group_id} 订阅：{name}\n"
        f"UID {uid}\n"
        f"轮询间隔约 {plugin_config.poll_interval_seconds}s"
    )
    if need_baseline:
        tip += "\n首轮只建立基线、不回推历史动态，之后的新动态才会推送"
    return tip + note


def do_unsubscribe(uid: str, group_id: int) -> str:
    """让指定群退订该 UP 主，返回回执文案"""
    name = store.get_name(uid) or "未知昵称"
    if not store.remove_subscription(uid, group_id):
        return f"群 {group_id} 未订阅 UID {uid}"

    # 已无任何群订阅时顺手丢掉退避状态，避免 dict 无界增长
    if uid not in store.get_all_uids():
        backoff_manager.forget(uid)

    logger.info(f"群 {group_id} 退订 UID {uid}")
    return f"✅ 已为群 {group_id} 退订：{name}\nUID {uid}"


def render_group_list(group_id: int) -> str:
    """某个群的订阅列表（群聊路径）"""
    items = store.list_subscriptions(group_id)
    if not items:
        return (
            f"群 {group_id} 暂无 B 站动态订阅\n"
            f"使用「b站订阅 <UID>」添加\n{_format_global_status()}"
        )

    lines = [f"📋 群 {group_id} 的 B 站动态订阅（{len(items)} 个）：", _format_global_status()]
    lines.extend(_format_subscription(item) for item in items)
    return "\n".join(lines)


def render_global_list() -> str:
    """全局订阅列表（私聊路径）：每条标出推送到哪些群，便于不进群也能核对订阅关系"""
    items = store.list_subscriptions()
    if not items:
        return f"当前没有任何 B 站动态订阅\n使用「b站订阅 <UID> <群号>」添加\n{_format_global_status()}"

    group_count = len({group_id for item in items for group_id in item.get("groups") or []})
    lines = [
        f"📋 全局 B 站动态订阅（{len(items)} 个 UP 主 / 覆盖 {group_count} 个群）：",
        _format_global_status(),
    ]
    lines.extend(_format_subscription(item, show_groups=True) for item in items)
    return "\n".join(lines)


# ---------------------------------------------------------------- 命令注册
#
# 每条命令只注册一个 matcher（不按 rule 拆私聊/群聊，否则启动会报 Duplicated prefix rule），
# 私聊 / 群聊行为由下方按事件类型注解分流的 handler 决定：
# NoneBot 会跳过事件类型与参数注解不匹配的 handler，接着尝试同一 matcher 的下一个 handler。
# permission 挂在 matcher 上，故私聊路径同样要求 SUPERUSER。

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


# ---------------------------------------------------------------- 订阅 / 退订


@bili_sub.handle()
async def handle_subscribe_group(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """群聊：b站订阅 <UID> [群号] —— 省略群号则作用于当前群"""
    uid_raw, group_raw = split_command_args(args.extract_plain_text())
    uid = parse_uid(uid_raw)
    if not uid:
        await bili_sub.finish(_uid_usage("b站订阅", private=False, url_form=True))

    group_id = event.group_id
    note = ""
    if group_raw:
        # 显式给了群号就以给的为准（在 A 群给 B 群加订阅）
        target = parse_group_id(group_raw)
        if target is None:
            await bili_sub.finish(_group_id_usage("b站订阅", group_raw, private=False))
        group_id = target
        if group_id != event.group_id:
            # 只有跨群才值得查一次群列表；作用于当前群时 bot 显然在群里
            note = await membership_warning(bot, group_id)

    await bili_sub.finish(await do_subscribe(uid, group_id, note=note))


@bili_sub.handle()
async def handle_subscribe_private(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """私聊：b站订阅 <UID> <群号> —— 群号必填，否则无从判断要改哪个群"""
    uid_raw, group_raw = split_command_args(args.extract_plain_text())
    uid = parse_uid(uid_raw)
    if not uid:
        await bili_sub.finish(_uid_usage("b站订阅", private=True, url_form=True))

    group_id = parse_group_id(group_raw)
    if group_id is None:
        await bili_sub.finish(_group_id_usage("b站订阅", group_raw, private=True))

    # 私聊的群号全靠手打，订阅前先确认 bot 真在那个群里（查不到则静默跳过）
    note = await membership_warning(bot, group_id)
    await bili_sub.finish(await do_subscribe(uid, group_id, note=note))


@bili_unsub.handle()
async def handle_unsubscribe_group(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    """群聊：b站退订 <UID> [群号] —— 省略群号则作用于当前群"""
    uid_raw, group_raw = split_command_args(args.extract_plain_text())
    uid = parse_uid(uid_raw)
    if not uid:
        await bili_unsub.finish(_uid_usage("b站退订", private=False))

    group_id = event.group_id
    if group_raw:
        target = parse_group_id(group_raw)
        if target is None:
            await bili_unsub.finish(_group_id_usage("b站退订", group_raw, private=False))
        group_id = target

    await bili_unsub.finish(do_unsubscribe(uid, group_id))


@bili_unsub.handle()
async def handle_unsubscribe_private(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    """私聊：b站退订 <UID> <群号> —— 群号必填"""
    uid_raw, group_raw = split_command_args(args.extract_plain_text())
    uid = parse_uid(uid_raw)
    if not uid:
        await bili_unsub.finish(_uid_usage("b站退订", private=True))

    group_id = parse_group_id(group_raw)
    if group_id is None:
        await bili_unsub.finish(_group_id_usage("b站退订", group_raw, private=True))

    await bili_unsub.finish(do_unsubscribe(uid, group_id))


# ---------------------------------------------------------------- 订阅列表


@bili_list.handle()
async def handle_list_group(bot: Bot, event: GroupMessageEvent):
    """群聊：b站订阅列表 —— 只列当前群的订阅"""
    await bili_list.finish(render_group_list(event.group_id))


@bili_list.handle()
async def handle_list_private(bot: Bot, event: PrivateMessageEvent):
    """私聊：b站订阅列表 —— 列出全局订阅，并标出每条推送到哪些群"""
    await bili_list.finish(render_global_list())


# ---------------------------------------------------------------- 排查命令


@bili_test.handle()
async def handle_test(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """b站订阅测试 <UID>：立即拉一次并回显解析结果，不推送、不改状态。

    纯只读且与群无关，故不按事件类型分流：群聊与私聊共用同一个 handler
    （`MessageEvent` 同时匹配 GroupMessageEvent 与 PrivateMessageEvent），
    超管可在私聊里验证插件是否正常，而不必打扰有真实订阅的生产群。
    """
    uid_raw, mode_raw = split_command_args(args.extract_plain_text())
    uid = parse_uid(uid_raw)
    if not uid:
        await bili_test.finish(
            "请提供要测试的 UP 主 UID，例如：\n"
            "b站订阅测试 13148307        （完整渲染，与真实推送一致）\n"
            "b站订阅测试 13148307 text   （纯文本，只看解析结果）"
        )

    text_only = mode_raw.strip().lower() in _TEXT_MODE_KEYWORDS
    await bili_test.send(f"正在拉取 UID {uid} 的动态...")

    try:
        data = await api.fetch_space_feed(uid)
    except api.BiliApiError as e:
        await bili_test.finish(f"❌ 取数失败：{e}\n{_format_global_status()}")
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"测试拉取 UID {uid} 失败: {get_exc_desc(e)}")
        await bili_test.finish(f"❌ 取数异常：{get_exc_desc(e)}")

    parsed_list = parse_feed(data)
    header = [
        f"✅ 取数成功：UID {uid} 共 {len(parsed_list)} 条动态",
        _format_global_status(),
        f"基线：{'已建立' if store.is_baseline_initialized(uid) else '未建立'}",
    ]

    if not parsed_list:
        header.append("（该 UP 当前没有可解析的动态）")
        await bili_test.finish("\n".join(header))

    if text_only:
        # 文本模式：只看解析结果，不渲染（快，且不占用浏览器）
        header.append("—— 模式：纯文本（解析预览）")
        # parse_feed 为升序，取尾部并倒序展示最新的几条
        for parsed in reversed(parsed_list[-_PREVIEW_MAX_ITEMS:]):
            header.append(_preview_head(uid, parsed))
            header.append(truncate(build_text(parsed), _PREVIEW_TEXT_LENGTH))
        await bili_test.finish("\n".join(header))

    # 渲染模式（默认）：把最新一条按**真实推送流程**渲染并发出来，所见即所得
    latest = parsed_list[-1]
    header.append("—— 模式：完整渲染（与真实推送逐条一致）")
    header.append(_preview_head(uid, latest))
    header.append("正在渲染，请稍候...")
    await bili_test.send("\n".join(header))

    try:
        segments = await build_messages(latest)
    except Exception as e:
        logger.error(f"测试渲染 UID {uid} 动态 {latest.dyn_id} 失败: {get_exc_desc(e)}")
        await bili_test.finish(
            f"❌ 渲染失败：{get_exc_desc(e)}\n"
            f"真实推送遇到同样情况会降级为纯文本（宁丑勿漏），文本内容如下：\n"
            f"{truncate(build_text(latest), _PREVIEW_TEXT_LENGTH)}"
        )

    # 复用 scheduler 的分发路径：首段单发 → 余下 1 段单发 / ≥2 段合并转发 + 全局间隔。
    # 预览必须与真实推送走同一条路，否则"预览没问题"证明不了"推到群里没问题"。
    target = (
        SendTarget(user_id=event.user_id)
        if isinstance(event, PrivateMessageEvent)
        else SendTarget(group_id=event.group_id)
    )
    await dispatch_segments(bot, target, segments)

    degraded = bool(segments) and segments[0].type == "text"
    tail = f"以上为 {len(segments)} 个消息段的实际推送效果"
    if degraded:
        tail += "\n⚠️ 文字卡片渲染失败，已降级为纯文本（真实推送同此行为，请检查 Playwright/字体）"
    await bili_test.finish(tail)


def _preview_head(uid: str, parsed) -> str:
    """预览用的一行动态摘要：id | 分类 | 特殊标记 | 是否已推送"""
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
