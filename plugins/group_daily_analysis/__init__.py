import asyncio
import os
import json
from pathlib import Path
from nonebot import require, on_command, on_message, get_bot, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException

require("nonebot_plugin_apscheduler")
require("plugins.plugin_manager")
from nonebot_plugin_apscheduler import scheduler

from plugins.plugin_manager.enable import is_plugin_enabled
from plugins.plugin_manager import plugin_status

from .src.config import plugin_config, save_config
from .src.analysis.main import MessageAnalyzer
from .src.render.renderer import ReportRenderer
from .src.data_source import MessageFetcher
from .src.database import db

__plugin_meta__ = PluginMetadata(
    name="群聊每日总结",
    description="分析群聊记录，生成每日总结报告（话题、活跃度、金句等）",
    usage="指令：/daily_analysis, /今日总结, /群日报\n设置：/设置模板, /查看模板",
    config=plugin_config.__class__
)

# --- 消息记录器 ---
# 优先级设为 10，确保不阻塞其他高优先级命令，但能记录所有消息
message_recorder = on_message(priority=10, block=False)

@message_recorder.handle()
async def record_message(bot: Bot, event: GroupMessageEvent):
    """记录群消息到数据库"""
    try:
        # 获取发送者昵称
        sender = event.sender
        if plugin_config.enable_user_card:
            nickname = sender.card or sender.nickname or "未知用户"
        else:
            nickname = sender.nickname or "未知用户"
        
        # 序列化消息链以保留完整结构 (表情、图片等)
        # event.message 是 Message 对象，转 list 后包含 Segment
        # 需要转为 JSON 存入 raw_message
        try:
            # Message 对象可以直接序列化为 JSON 兼容的 list
            msg_list = []
            for seg in event.message:
                if seg.type == "text":
                    msg_list.append({"type": "text", "data": {"text": str(seg)}})
                elif seg.type == "face":
                    msg_list.append({"type": "face", "data": {"id": seg.data.get("id")}})
                elif seg.type == "at":
                    msg_list.append({"type": "at", "data": {"qq": seg.data.get("qq")}})
                # 其他类型暂存为 text 或忽略
                
            raw_message = json.dumps(msg_list, ensure_ascii=False)
        except Exception:
            raw_message = ""

        db.add_message(
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

# --- 分析命令 ---
analysis_cmd = on_command("daily_analysis", aliases={"今日总结", "群日报"}, permission=SUPERUSER, priority=5, block=True)
debug_analysis_cmd = on_command("debug_daily_analysis", aliases={"日报调试"}, permission=SUPERUSER, priority=5, block=True)

async def run_analysis(bot: Bot, group_id: int, retries: int = 3, debug: bool = False):
    """
    运行分析任务并发送结果 (带重试机制)
    """
    logger.info(f"开始分析群 {group_id} 的每日总结 (Debug={debug})...")
    
    last_error = None
    for i in range(retries):
        try:
            if i > 0:
                logger.info(f"第 {i+1} 次重试群 {group_id} 的分析任务...")
            
            # 1. 获取消息 (从数据库)
            fetcher = MessageFetcher()
            messages = await fetcher.fetch_messages(bot, group_id)
            
            # Debug 模式下忽略消息数量限制
            if not debug and len(messages) < plugin_config.min_messages_threshold:
                logger.warning(f"群 {group_id} 消息数量不足 ({len(messages)} < {plugin_config.min_messages_threshold})，跳过分析")
                return None

            # 2. 分析消息
            analyzer = MessageAnalyzer()
            result = await analyzer.analyze_messages(messages, str(group_id), debug_mode=debug)
            
            # 3. 渲染报告
            renderer = ReportRenderer()
            image_bytes = await renderer.render_to_image(result, str(group_id))
            
            return image_bytes
            
        except Exception as e:
            logger.warning(f"群 {group_id} 分析失败 (尝试 {i+1}/{retries}): {e}")
            last_error = e
            # 简单的指数退避
            await asyncio.sleep(2 * (i + 1))
    
    if last_error:
        logger.error(f"群 {group_id} 分析最终失败: {last_error}")
        # 这里可以选择抛出异常或者返回 None
        # 如果抛出，外层可以捕获并提示用户
        raise last_error
    return None

@analysis_cmd.handle()
async def handle_analysis(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    
    if not is_plugin_enabled("group_daily_analysis", str(group_id), str(event.user_id)):
        return

    await analysis_cmd.send("正在生成每日总结，请稍候... (可能需要几十秒)")
    
    try:
        image_bytes = await run_analysis(bot, group_id, retries=2) # 手动触发重试2次
        
        if image_bytes:
            await analysis_cmd.finish(MessageSegment.image(image_bytes))
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
    logger.info("正在清理过期消息...")
    db.cleanup_old_messages(days=7)

    try:
        bot = get_bot()
    except ValueError:
        logger.warning("未连接 Bot，跳过定时任务")
        return

    target_groups = []
    # 从 plugin_manager 获取启用的群列表
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
                await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(image_bytes))
            
            # 避免并发过高
            import asyncio
            await asyncio.sleep(10) 
            
        except Exception as e:
            logger.error(f"群 {group_id_str} 自动总结失败: {e}")

# 注册定时任务
if plugin_config.enable_auto_analysis:
    hour, minute = plugin_config.auto_analysis_time.split(":")
    scheduler.add_job(
        auto_run_daily_analysis, 
        "cron", 
        hour=int(hour), 
        minute=int(minute),
        id="group_daily_analysis_job"
    )
    logger.info(f"已注册每日总结定时任务: {plugin_config.auto_analysis_time}")
