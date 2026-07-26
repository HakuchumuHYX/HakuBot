import asyncio
import os
import json
from pathlib import Path
import time
from collections import defaultdict

from nonebot import require, on_command, on_message, on_type, get_bot, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Event as OneBotEvent
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

require("nonebot_plugin_apscheduler")
require("plugins.plugin_manager")
from nonebot_plugin_apscheduler import scheduler

from plugins.plugin_manager.enable import is_plugin_enabled, is_feature_enabled
from plugins.plugin_manager import plugin_status

from .src.config import plugin_config, save_config
from .src.analysis.main import MessageAnalyzer
from .src.render.renderer import ReportRenderer
from .src.data_source import MessageFetcher
from .src.database import db

# --- 过滤本插件发出的“日报总结”消息（通过 message_id 精确过滤，避免递归污染） ---
# group_id -> {message_id -> timestamp}
_REPORT_MESSAGE_TTL_SECONDS = 3600  # 1h 以内认为是“刚发出的日报总结”
_recent_report_message_ids: dict[int, dict[int, float]] = defaultdict(dict)


def _mark_report_message_id(group_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    _recent_report_message_ids[int(group_id)][int(message_id)] = time.time()


def _is_recent_report_message_id(group_id: int, message_id: int | None) -> bool:
    if not message_id:
        return False
    gid = int(group_id)
    mid = int(message_id)
    now = time.time()

    # 清理过期
    bucket = _recent_report_message_ids.get(gid)
    if not bucket:
        return False
    expired = [k for k, ts in bucket.items() if now - ts > _REPORT_MESSAGE_TTL_SECONDS]
    for k in expired:
        bucket.pop(k, None)

    return mid in bucket

__plugin_meta__ = PluginMetadata(
    name="群聊每日总结",
    description="分析群聊记录，生成每日总结报告（话题、活跃度、金句等）",
    usage="指令：/daily_analysis, /今日总结, /群日报\n设置：/设置模板, /查看模板",
    config=plugin_config.__class__
)

# --- 消息记录器 ---
# 优先级设为 1，确保不阻塞其他命令，但能记录所有消息
message_recorder = on_message(priority=1, block=False)

# --- Bot 自己发出的群消息回流事件记录器 (post_type=message_sent) ---
message_sent_recorder = on_type(
    OneBotEvent,
    rule=lambda event: getattr(event, "post_type", None) == "message_sent"
    and getattr(event, "message_type", None) == "group",
    priority=10,
    block=False,
)

@message_recorder.handle()
async def record_message(bot: Bot, event: GroupMessageEvent):
    """记录群消息到数据库"""
    # 群里显式禁用本插件时不记录（user_id 传 "0"，避免 superuser 旁路导致开关失效）
    if not is_plugin_enabled("group_daily_analysis", str(event.group_id), "0"):
        return
    try:
        # 获取发送者昵称
        sender = event.sender
        if plugin_config.enable_user_card:
            nickname = sender.card or sender.nickname or "未知用户"
        else:
            nickname = sender.nickname or "未知用户"
        
        # 序列化消息链以保留完整结构 (表情、图片等)
        # 完整保留所有消息类型和数据
        try:
            msg_list = []
            for seg in event.message:
                # 通用序列化：保留所有类型和完整数据
                msg_list.append({
                    "type": seg.type,
                    "data": dict(seg.data)  # 保留完整的 data 字典
                })
            raw_message = json.dumps(msg_list, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"消息序列化失败: {e}")
            raw_message = ""

        await asyncio.to_thread(
            db.add_message,
            group_id=str(event.group_id),
            user_id=str(event.user_id),
            sender_name=nickname,
            content=event.get_plaintext(),
            timestamp=int(event.time),
            msg_type="group",
            raw_message=raw_message
        )
    except Exception as e:
        # 记录失败不应影响主流程，仅打日志
        # logger.debug(f"记录消息失败: {e}")
        pass


@message_sent_recorder.handle()
async def record_message_sent(bot: Bot, event: OneBotEvent):
    """
    记录 bot 自己发出的群消息到数据库。

    说明：
    - OneBot V11 会把 self 发送的消息以 post_type=message_sent 回流
    - 当前 nonebot onebot v11 adapter 没有专门的 MessageSentEvent 类型，因此用 on_type(Event)+rule 过滤
    - 会精确过滤掉本插件发出的“日报总结”消息（通过 message_id）
    """
    try:
        group_id = int(getattr(event, "group_id"))

        # 群里显式禁用本插件时不记录（user_id 传 "0"，避免 superuser 旁路导致开关失效）
        if not is_plugin_enabled("group_daily_analysis", str(group_id), "0"):
            return

        message_id = int(getattr(event, "message_id", 0) or 0)

        # 跳过本插件发出的日报总结，避免“总结套娃”
        if _is_recent_report_message_id(group_id, message_id):
            return

        sender = getattr(event, "sender", None) or {}
        user_id = getattr(event, "user_id", None) or sender.get("user_id") or 0
        sender_name = (
            (sender.get("card") or sender.get("nickname"))
            if isinstance(sender, dict)
            else getattr(sender, "card", None) or getattr(sender, "nickname", None)
        )
        sender_name = sender_name or "未知用户"

        # 序列化消息链（尽量保留结构）
        raw_message = ""
        try:
            msg_list = []
            message = getattr(event, "message", None)
            if message is not None:
                for seg in message:
                    # seg 可能是 MessageSegment 或 dict
                    if hasattr(seg, "type") and hasattr(seg, "data"):
                        msg_list.append({"type": seg.type, "data": dict(seg.data)})
                    elif isinstance(seg, dict):
                        msg_list.append({"type": seg.get("type"), "data": dict(seg.get("data") or {})})
            raw_message = json.dumps(msg_list, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"message_sent 序列化失败: {e}")
            raw_message = ""

        content = ""
        try:
            content = getattr(event, "raw_message", None) or ""
        except Exception:
            content = ""

        await asyncio.to_thread(
            db.add_message,
            group_id=str(group_id),
            user_id=str(user_id),
            sender_name=sender_name,
            content=content,
            timestamp=int(getattr(event, "time")),
            msg_type="group_sent",
            raw_message=raw_message,
        )
    except Exception as e:
        logger.debug(f"记录 message_sent 失败: {e}")
        return

# --- 分析命令 ---
analysis_cmd = on_command("daily_analysis", aliases={"今日总结", "群日报"}, permission=SUPERUSER, priority=5, block=True)
debug_analysis_cmd = on_command("debug_daily_analysis", aliases={"日报调试"}, permission=SUPERUSER, priority=5, block=True)

async def run_analysis(bot: Bot, group_id: int, retries: int = 3, debug: bool = False):
    """
    运行分析任务并发送结果 (带分阶段重试机制)
    
    流程分为三个独立阶段，每个阶段可独立重试：
    1. 获取消息（从数据库）
    2. LLM 分析（生成 AnalysisResult）
    3. 渲染报告（生成图片）
    
    这样可以避免渲染失败时重新执行 LLM 分析（浪费 token）。
    """
    logger.info(f"开始分析群 {group_id} 的每日总结 (Debug={debug})...")
    
    # === 阶段 1: 获取消息 ===
    messages = None
    fetch_error = None
    for i in range(retries):
        try:
            if i > 0:
                logger.info(f"第 {i+1} 次重试获取群 {group_id} 消息...")
            
            fetcher = MessageFetcher()
            messages = await fetcher.fetch_messages(bot, group_id)
            break  # 成功则跳出
            
        except Exception as e:
            logger.warning(f"获取群 {group_id} 消息失败 (尝试 {i+1}/{retries}): {e}")
            fetch_error = e
            await asyncio.sleep(1 * (i + 1))
    
    if messages is None:
        logger.error(f"群 {group_id} 消息获取最终失败")
        if fetch_error:
            raise fetch_error
        return None
    
    # Debug 模式下忽略消息数量限制
    if not debug and len(messages) < plugin_config.min_messages_threshold:
        logger.warning(f"群 {group_id} 消息数量不足 ({len(messages)} < {plugin_config.min_messages_threshold})，跳过分析")
        return None
    
    logger.info(f"群 {group_id} 获取到 {len(messages)} 条消息")

    # === 阶段 2: LLM 分析 ===
    # 子任务内部已经有独立重试机制（_run_subtask_with_retry），
    # 外层仅在"全部为空"时才触发整体重试（避免浪费 token）。
    analysis_result = None
    analysis_error = None
    for i in range(retries):
        try:
            if i > 0:
                logger.info(f"第 {i+1} 次重试群 {group_id} 的 LLM 分析...")
            
            analyzer = MessageAnalyzer()
            analysis_result = await analyzer.analyze_messages(messages, str(group_id), debug_mode=debug)
            
            # 按实际开启的分析项检查完整性
            expected_items: dict[str, list] = {}
            if plugin_config.topic_analysis_enabled and is_feature_enabled("group_daily_analysis", "topics", str(group_id), "0"):
                expected_items["topics"] = analysis_result.topics
            if plugin_config.user_title_analysis_enabled and is_feature_enabled("group_daily_analysis", "user_titles", str(group_id), "0"):
                expected_items["user_titles"] = analysis_result.user_titles
            if plugin_config.golden_quote_analysis_enabled and is_feature_enabled("group_daily_analysis", "golden_quotes", str(group_id), "0"):
                expected_items["golden_quotes"] = analysis_result.golden_quotes

            filled = {k: v for k, v in expected_items.items() if v}
            missing = [k for k, v in expected_items.items() if not v]
            
            if not filled and not debug:
                # 全部为空 → 触发外层重试
                logger.warning(
                    f"群 {group_id} LLM 分析返回全空结果 (缺失: {missing})，触发整体重试"
                )
                if i < retries - 1:
                    await asyncio.sleep(2 * (i + 1))
                    continue
            elif missing and not debug:
                # 部分缺失 → 警告但继续渲染（避免因单项反复失败而浪费更多 token）
                logger.warning(
                    f"群 {group_id} LLM 分析部分缺失: {missing}，已有: {list(filled.keys())}。"
                    f"子任务内部已重试过，继续渲染。"
                )
            
            break  # 有内容或已耗尽重试次数
            
        except Exception as e:
            logger.warning(f"群 {group_id} LLM 分析失败 (尝试 {i+1}/{retries}): {e}")
            analysis_error = e
            await asyncio.sleep(2 * (i + 1))
    
    if analysis_result is None:
        logger.error(f"群 {group_id} LLM 分析最终失败")
        if analysis_error:
            raise analysis_error
        return None
    
    # 记录分析结果统计
    logger.info(
        f"群 {group_id} 分析完成: "
        f"话题={len(analysis_result.topics)}, "
        f"称号={len(analysis_result.user_titles)}, "
        f"金句={len(analysis_result.golden_quotes)}"
    )

    # === 阶段 3: 渲染报告 ===
    image_bytes = None
    render_error = None
    for i in range(retries):
        try:
            if i > 0:
                logger.info(f"第 {i+1} 次重试群 {group_id} 的报告渲染...")
            
            renderer = ReportRenderer()
            image_bytes = await renderer.render_to_image(analysis_result, str(group_id))
            break  # 成功则跳出
            
        except Exception as e:
            logger.warning(f"群 {group_id} 报告渲染失败 (尝试 {i+1}/{retries}): {e}")
            render_error = e
            await asyncio.sleep(1 * (i + 1))
    
    if image_bytes is None:
        logger.error(f"群 {group_id} 报告渲染最终失败")
        if render_error:
            raise render_error
        return None
    
    logger.info(f"群 {group_id} 每日总结生成成功，图片大小: {len(image_bytes)} bytes")
    return image_bytes

@analysis_cmd.handle()
async def handle_analysis(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_plugin_enabled("group_daily_analysis", str(group_id), str(event.user_id)):
        return

    await analysis_cmd.send("正在生成每日总结，请稍候... (可能需要几十秒)")
    
    try:
        image_bytes = await run_analysis(bot, group_id, retries=2) # 手动触发重试2次
        
        if image_bytes:
            # 用 send_group_msg 发送以拿到 message_id，用于过滤本插件发出的总结
            resp = await bot.send_group_msg(
                group_id=group_id, message=MessageSegment.image(image_bytes)
            )
            if isinstance(resp, dict):
                _mark_report_message_id(group_id, resp.get("message_id"))
            await analysis_cmd.finish()
        else:
            await analysis_cmd.finish(f"消息数量不足 {plugin_config.min_messages_threshold} 条，无法生成总结。")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"生成总结失败: {e}")
        await analysis_cmd.finish(f"生成总结失败，请稍后重试。")

@debug_analysis_cmd.handle()
async def handle_debug_analysis(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    await debug_analysis_cmd.send("🧪 正在运行 Debug 模式分析 (使用 Mock 数据)...")
    
    try:
        image_bytes = await run_analysis(bot, group_id, retries=1, debug=True)
        
        if image_bytes:
            await debug_analysis_cmd.finish(MessageSegment.image(image_bytes))
        else:
            await debug_analysis_cmd.finish("Debug 分析生成失败，未返回图片。")
            
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Debug 分析失败: {e}")
        await debug_analysis_cmd.finish(f"Debug 分析失败: {e}")

# --- 模板管理命令 ---
set_template_cmd = on_command("设置模板", permission=SUPERUSER, priority=5, block=True)
view_template_cmd = on_command("查看模板", permission=SUPERUSER, priority=5, block=True)

TEMPLATES_DIR = Path(__file__).parent / "src" / "render" / "templates"

def get_available_templates():
    if not TEMPLATES_DIR.exists():
        return []
    return [d.name for d in TEMPLATES_DIR.iterdir() if d.is_dir() and not d.name.startswith("__")]

@view_template_cmd.handle()
async def handle_view_templates(bot: Bot, event: GroupMessageEvent):
    templates = get_available_templates()
    if not templates:
        await view_template_cmd.finish("未找到任何可用模板。")
    
    current = plugin_config.report_template
    msg = "🎨 可用模板列表：\n"
    for t in templates:
        mark = "✅ " if t == current else "   "
        msg += f"{mark}{t}\n"
    
    msg += "\n使用 /设置模板 [模板名] 进行切换"
    await view_template_cmd.finish(msg)

@set_template_cmd.handle()
async def handle_set_template(bot: Bot, event: GroupMessageEvent):
    args = event.get_plaintext().strip().replace("设置模板", "").strip()
    if not args:
        await set_template_cmd.finish("请指定模板名称。使用 /查看模板 查看可用列表。")
    
    templates = get_available_templates()
    if args not in templates:
        await set_template_cmd.finish(f"模板 '{args}' 不存在。")
        
    plugin_config.report_template = args
    save_config(plugin_config)
    await set_template_cmd.finish(f"✅ 已切换模板为: {args}")

# --- 定时任务 ---
async def auto_run_daily_analysis():
    if not plugin_config.enable_auto_analysis:
        return

    logger.info("开始运行每日自动总结任务...")

    # 1. 清理过期消息 (保留7天)
    # 注意：清理失败不应中断自动总结流程
    try:
        logger.info("正在清理过期消息...")
        db.cleanup_old_messages(retention_days=7)
    except Exception as e:
        logger.warning(f"清理过期消息失败(将继续执行自动总结): {e}")

    try:
        bot = get_bot()
    except ValueError:
        logger.warning("未连接 Bot，跳过定时任务")
        return

    target_groups = []
    # 从 plugin_manager 获取启用的群列表
    # 设计决策：自动日报只推送给 plugin_status 中显式开启（True）的群，
    # 与手动命令的“默认启用”语义不同，避免向未主动开启的群推送日报
    if "group_daily_analysis" in plugin_status:
        for gid, enabled in plugin_status["group_daily_analysis"].items():
            if enabled:
                target_groups.append(gid)

    if not target_groups:
        logger.info("没有群开启了每日总结插件，跳过任务")
        return

    for group_id_str in target_groups:
        try:
            group_id = int(group_id_str)
            image_bytes = await run_analysis(bot, group_id, retries=3) # 自动任务重试3次
            if image_bytes:
                resp = await bot.send_group_msg(
                    group_id=group_id, message=MessageSegment.image(image_bytes)
                )
                if isinstance(resp, dict):
                    _mark_report_message_id(group_id, resp.get("message_id"))
            
            # 避免并发过高
            import asyncio
            await asyncio.sleep(10) 
            
        except Exception as e:
            logger.error(f"群 {group_id_str} 自动总结失败: {e}")

def cleanup_old_messages_job():
    """独立的消息清理任务（与自动总结解耦）"""
    try:
        db.cleanup_old_messages(retention_days=7)
    except Exception as e:
        logger.warning(f"定时清理过期消息失败: {e}")


# 注册：独立清理任务（即使不开启自动总结也会执行，避免 DB 无限增长）
# 每天凌晨 04:00 清理一次
scheduler.add_job(
    cleanup_old_messages_job,
    "cron",
    hour=4,
    minute=0,
    id="group_daily_analysis_cleanup_job",
    replace_existing=True,
)
logger.info("已注册 group_daily_analysis 消息清理定时任务: 04:00 (保留7天)")

# 注册：自动总结任务
if plugin_config.enable_auto_analysis:
    hour, minute = plugin_config.auto_analysis_time.split(":")
    scheduler.add_job(
        auto_run_daily_analysis,
        "cron",
        hour=int(hour),
        minute=int(minute),
        id="group_daily_analysis_job",
        replace_existing=True,
    )
    logger.info(f"已注册每日总结定时任务: {plugin_config.auto_analysis_time}")
