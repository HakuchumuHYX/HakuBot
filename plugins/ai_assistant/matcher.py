import time
from typing import NoReturn
from collections.abc import Awaitable

from openai import APIStatusError, BadRequestError
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from utils.onebot.media import image_segment
from utils.onebot.rules import command_boundary
from utils.logging import get_exc_desc
from utils.llm.client import chat_completion
from plugins.ai_assistant.chat import (
    MAX_SEARCHES,
    build_chat_messages,
    chat_llm_config,
    chat_search,
    load_thread,
    run_tool_loop,
    save_thread,
)
from plugins.ai_assistant.config import plugin_config
from plugins.ai_assistant.imagen import draw, draw_from_plan, research
from plugins.ai_assistant.render import render_reply
from plugins.ai_assistant.utils import (
    extract_pure_text,
    parse_message_content,
    remove_markdown,
)

from core.access import switch_rule
from core.cooldown import cooldown_remaining, mark_cooldown

CHAT_ID = "ai_assistant:chat"
IMAGEN_ID = "ai_assistant:imagen"

# 注册命令
chat_matcher = on_command(
    "chat", rule=command_boundary & switch_rule(CHAT_ID), priority=5, block=True
)

draw_matcher = on_command(
    "生图", rule=command_boundary & switch_rule(IMAGEN_ID), priority=5, block=True
)
draw_web_matcher = on_command(
    "生图联网",
    aliases={"生图web", "生图搜索"},
    rule=command_boundary & switch_rule(IMAGEN_ID),
    priority=5,
    block=True,
)

model_cmd = on_command(
    "切换模型",
    aliases={"更改模型", "change_model"},
    permission=SUPERUSER,
    priority=5,
    block=True,
)


async def _render_chat_reply(
    reply_text: str, stat_text: str
) -> MessageSegment | Message:
    watermark = plugin_config.chat.watermark
    md_content = reply_text + f"\n\n---\n*{stat_text}*"
    if watermark:
        watermark_html = watermark.replace("\n", "<br>")
        md_content += (
            "\n\n<div align='right' "
            "style='color: gray; font-size: 0.9em; font-style: italic;'>"
            f"{watermark_html}</div>"
        )

    try:
        img_bytes = await render_reply(md_content)
        return image_segment(img_bytes)
    except Exception:
        logger.opt(exception=True).warning("渲染 Markdown 失败，改发纯文本")
        return Message(remove_markdown(reply_text) + f"\n\n{stat_text}")


async def _finish_failed(
    matcher: type[Matcher], exc: Exception, log_context: str, error_prefix: str
) -> NoReturn:
    """在 except 块里调用：按异常类型记日志，并把错误回复给用户"""
    if isinstance(exc, BadRequestError):
        # 上游拒绝这条请求（内容审核、输入过长等），是对用户输入的判定，不算故障
        logger.warning(f"{log_context}: 请求被上游拒绝: {get_exc_desc(exc)}")
        await matcher.finish(f"{error_prefix}: {exc}")
    if isinstance(exc, APIStatusError) and exc.status_code >= 500:
        # 上游故障（含 524、CPA 冷却期的 auth_unavailable），原始 JSON 对群友没有意义
        logger.warning(f"{log_context}: 上游不可用: {get_exc_desc(exc)}")
        await matcher.finish(
            f"{error_prefix}: 上游服务暂时不可用（HTTP {exc.status_code}），请稍后再试"
        )
    logger.exception(log_context)
    await matcher.finish(f"{error_prefix}: {exc}")


def _thread_session(event: MessageEvent) -> str:
    # 不用 event.get_session_id()：群里它带用户 id，别人就续不了这条线
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}"
    return f"private_{event.user_id}"


@chat_matcher.handle()
async def handle_chat(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    cd_remain = cooldown_remaining(CHAT_ID, event)
    if cd_remain > 0:
        await chat_matcher.finish(
            f"Chat功能冷却中，请等待 {cd_remain} 秒", at_sender=True
        )

    session = _thread_session(event)
    history = load_thread(session, event.reply.message_id) if event.reply else None
    try:
        # 接上历史时模型读的是历史原文，不再解析被回复的那张回答截图
        content_list = await parse_message_content(
            event, args, bot=bot, quote=history is None
        )
    except Exception as exc:
        await _finish_failed(chat_matcher, exc, "Chat Error", "发生错误")
    if not content_list:
        await chat_matcher.finish("请提供对话内容，或回复包含内容的消息。")

    await chat_matcher.send("正在思考中...")
    history = history or []
    messages = build_chat_messages(history, content_list)
    try:
        result = await run_tool_loop(
            messages, search=chat_search, max_searches=MAX_SEARCHES
        )
    except Exception as exc:
        await _finish_failed(chat_matcher, exc, "Chat Error", "发生错误")
    # LLM 调用成功（已产生消耗）后再更新 CD
    mark_cooldown(CHAT_ID, event)

    stat_text = (
        f"—— 使用模型: {result.model}"
        f" | Token消耗: {result.usage.total_tokens}"
        f" | 耗时: {result.elapsed:.2f}s"
    )
    if result.searches:
        stat_text += f" | 搜索 {result.searches} 次"
    if history:
        stat_text += f" | 第 {len(history) // 2 + 1} 轮"

    reply_msg = await _render_chat_reply(result.content, stat_text)
    receipt = await chat_matcher.send(reply_msg, at_sender=True)
    save_thread(
        session,
        receipt["message_id"],
        history
        + [
            {"role": "user", "content": content_list},
            {"role": "assistant", "content": result.content},
        ],
    )


async def _parse_draw_input(
    matcher: type[Matcher], event: MessageEvent, args: Message, error_prefix: str
) -> list:
    cd_remain = cooldown_remaining(IMAGEN_ID, event)
    if cd_remain > 0:
        await matcher.finish(f"生图功能冷却中，请等待 {cd_remain} 秒", at_sender=True)
    try:
        content_list = await parse_message_content(
            event, args, image_purpose="generation"
        )
    except Exception as exc:
        await _finish_failed(matcher, exc, "Draw Error", error_prefix)
    if not content_list:
        await matcher.finish("请提供文字描述，或回复一张图片。")
    return content_list


async def _draw_and_send(
    matcher: type[Matcher],
    event: MessageEvent,
    request: Awaitable[str],
    error_prefix: str,
) -> str:
    """等生图完成并发出图片，返回统计文本"""
    try:
        started_at = time.perf_counter()
        image_url = await request
        generation_elapsed = time.perf_counter() - started_at
        # 生图调用成功（已产生消耗）后再更新 CD
        mark_cooldown(IMAGEN_ID, event)

        started_at = time.perf_counter()
        await matcher.send(image_segment(image_url))
        send_elapsed = time.perf_counter() - started_at
    except Exception as exc:
        await _finish_failed(matcher, exc, "Draw Error", error_prefix)

    return (
        f"使用模型：{plugin_config.image.model}\n"
        f"生成耗费{generation_elapsed:.2f}s，发送耗费{send_elapsed:.2f}s"
    )


@draw_matcher.handle()
async def handle_draw(event: MessageEvent, args: Message = CommandArg()):
    content_list = await _parse_draw_input(draw_matcher, event, args, "生图失败")
    await draw_matcher.send("正在绘制中，请稍候...")
    stat_text = await _draw_and_send(
        draw_matcher, event, draw(content_list), "生图失败"
    )
    await draw_matcher.finish(stat_text)


@draw_web_matcher.handle()
async def handle_draw_web(event: MessageEvent, args: Message = CommandArg()):
    error_prefix = "联网生图失败"
    content_list = await _parse_draw_input(draw_web_matcher, event, args, error_prefix)
    if not extract_pure_text(content_list).strip():
        await draw_web_matcher.finish("未检测到可用于搜索的文本内容。")

    await draw_web_matcher.send("正在搜索资料...")
    try:
        plan = await research(content_list)
    except Exception as exc:
        await _finish_failed(draw_web_matcher, exc, "Draw Web Error", error_prefix)

    await draw_web_matcher.send("正在绘制...")
    stat_text = await _draw_and_send(
        draw_web_matcher, event, draw_from_plan(plan), error_prefix
    )
    if plan.vision_failed:
        stat_text += f"\n联网：搜索 {plan.searches} 次，视觉提炼失败，仅用文字资料"
    else:
        stat_text += f"\n联网：搜索 {plan.searches} 次，参考图 {plan.web_refs} 张"
    await draw_web_matcher.finish(stat_text)


@model_cmd.handle()
async def handle_change_model(args: Message = CommandArg()):
    new_model = args.extract_plain_text().strip()
    if not new_model:
        await model_cmd.finish("请提供新的模型名称。例如：切换模型 gpt-4")

    old_model = plugin_config.chat.model
    if old_model == new_model:
        await model_cmd.finish(f"当前已经是 {new_model} 模型了。")

    await model_cmd.send(
        f"正在尝试切换到模型: {new_model}\n正在进行连接测试，请稍候..."
    )

    messages = [{"role": "user", "content": "Hello! This is a connection test."}]
    try:
        # 直接用新模型发起测试请求，不修改全局配置，避免测试期间并发请求用到未验证的模型
        result = await chat_completion(chat_llm_config(new_model), messages)
    except Exception as e:
        logger.warning(f"切换模型 {new_model} 测试失败: {get_exc_desc(e)}")
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."

        await model_cmd.finish(
            f"❌ 切换失败，模型 {new_model} 似乎不可用。\n"
            f"当前模型保持为: {old_model}\n"
            f"错误信息: {error_msg}"
        )

    from core.settings import update_fields
    from plugins.ai_assistant.config import CONFIG_PATH

    # 先落盘再改内存：写配置失败时内存里仍是旧模型，和磁盘一致
    update_fields(CONFIG_PATH, {"chat": {"model": new_model}})
    plugin_config.chat.model = new_model

    # 截取简短的响应预览
    reply_text = result.content
    preview = reply_text[:50] + "..." if len(reply_text) > 50 else reply_text
    preview = preview.replace("\n", " ")

    await model_cmd.finish(
        f"✅ 模型切换成功！\n"
        f"旧模型: {old_model}\n"
        f"新模型: {result.model}\n"
        f"测试响应: {preview}"
    )
