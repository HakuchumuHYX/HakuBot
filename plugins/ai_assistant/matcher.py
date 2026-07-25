import time
from pathlib import Path

import aiofiles
from nonebot import get_driver, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from ..utils.browser import md_to_pic, read_tpl
from .config import plugin_config, save_config
from .utils import extract_pure_text, parse_message_content, remove_markdown
from .services.chat_service import call_chat_completion
from .services.chat_harness import decide_chat_search, prepare_chat_messages
from .services.imagen_service import call_image_generation
from .services.search_service import (
    build_visual_brief_from_search,
    compile_image_prompt_from_visual_brief,
    web_image_search_with_rewrite,
)

try:
    from ..plugin_manager.enable import is_feature_enabled
    from ..plugin_manager.cd_manager import check_cd, update_cd

    MANAGER_AVAILABLE = True
except ImportError:
    logger.warning("未找到 plugin_manager 插件，将跳过管理功能检查。")
    MANAGER_AVAILABLE = False

PLUGIN_NAME = "ai_assistant"

# 自定义 CSS 生成路径
CUSTOM_CSS_DIR = Path("data/ai_assistant")
CUSTOM_CSS_PATH = CUSTOM_CSS_DIR / "custom_markdown.css"

@get_driver().on_startup
async def generate_custom_css():
    """机器人启动时生成合并好的自定义 Markdown CSS"""
    try:
        # 获取基础样式
        base_css = await read_tpl("github-markdown-light.css")
        highlight_css = await read_tpl("pygments-default.css")
        
        # 加上配置的背景颜色覆盖
        bg_color = plugin_config.chat.bg_color
        custom_css = f"""
{base_css}
{highlight_css}

/* 自定义背景颜色 */
html, body, .markdown-body {{
    background-color: {bg_color} !important;
}}
"""
        # 确保目录存在
        CUSTOM_CSS_DIR.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(CUSTOM_CSS_PATH, "w", encoding="utf-8") as f:
            await f.write(custom_css)
            
        logger.info(f"已生成自定义 Markdown 背景颜色 CSS，背景色: {bg_color}")
    except Exception as e:
        logger.error(f"生成自定义 CSS 失败: {e}")

# 注册命令
chat_matcher = on_command("chat", priority=5, block=True)
chat_web_matcher = on_command("chat联网", aliases={"chat_web", "chatweb", "chat搜索"}, priority=5, block=True)

draw_matcher = on_command("生图", priority=5, block=True)
draw_web_matcher = on_command("生图联网", aliases={"生图web", "生图搜索"}, priority=5, block=True)

model_cmd = on_command("切换模型", aliases={"更改模型", "change_model"}, permission=SUPERUSER, priority=1, block=True)


async def _enforce_group_access(
    matcher: type[Matcher],
    event: MessageEvent,
    *,
    feature: str,
    display_name: str,
) -> None:
    """Apply the plugin-manager feature gate and cooldown for group commands."""
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        if not is_feature_enabled(PLUGIN_NAME, feature, group_id, user_id):
            await matcher.finish()

        cd_key = f"{PLUGIN_NAME}:{feature}"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await matcher.finish(
                f"{display_name}功能冷却中，请等待 {cd_remain} 秒",
                at_sender=True,
            )

        update_cd(cd_key, group_id, user_id)


async def _render_chat_reply(reply_text: str, stat_text: str) -> MessageSegment | Message:
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
        css_path = str(CUSTOM_CSS_PATH.absolute()) if CUSTOM_CSS_PATH.exists() else ""
        img_bytes = await md_to_pic(md=md_content, width=800, css_path=css_path)
        return MessageSegment.image(img_bytes)
    except Exception as exc:
        logger.error(f"渲染 Markdown 失败: {exc}")
        return Message(remove_markdown(reply_text) + f"\n\n{stat_text}")


async def _handle_chat_command(
    matcher: type[Matcher],
    bot: Bot,
    event: MessageEvent,
    args: Message,
    *,
    force_search: bool,
) -> None:
    await _enforce_group_access(
        matcher,
        event,
        feature="chat",
        display_name="Chat",
    )

    error_prefix = "联网Chat失败" if force_search else "发生错误"
    log_context = "Chat Web Error" if force_search else "Chat Error"

    try:
        content_list = await parse_message_content(
            bot,
            event,
            args,
            include_forward=True,
        )
        if not content_list:
            await matcher.finish("请提供对话内容，或回复包含内容的消息。")

        decision = decide_chat_search(content_list, force_search=force_search)
        if force_search and not decision.search_text:
            await matcher.finish("未检测到可用于搜索的文本内容。")
        if decision.mode != "none":
            await matcher.send("正在联网搜索中...")

        harness = await prepare_chat_messages(content_list, decision=decision)
        if force_search and harness.search_error:
            await matcher.finish(f"联网Chat失败: {harness.search_error}")

        await matcher.send("正在思考中...")
        result = await call_chat_completion(
            harness.messages,
            temperature=plugin_config.chat.temperature,
            top_p=plugin_config.chat.top_p,
        )

        stat_text = (
            f"—— 使用模型: {result.model}"
            f" | Token消耗: {result.usage.total_tokens}"
            f" | 耗时: {result.elapsed:.2f}s"
        )
        if force_search:
            stat_text += (
                f" | 联网: Tavily {harness.search_mode}"
                f" | Query数: {len(harness.queries)}"
            )
        elif harness.search_mode != "none":
            search_state = f"自动{harness.search_mode}"
            if harness.search_error:
                search_state += "失败"
            stat_text += f" | 联网: {search_state} | Query数: {len(harness.queries)}"

        reply_msg = await _render_chat_reply(result.content, stat_text)
        await matcher.finish(reply_msg, at_sender=True)

    except FinishedException:
        raise
    except Exception as exc:
        logger.exception(log_context)
        await matcher.finish(f"{error_prefix}: {exc}")


async def _handle_draw_command(
    matcher: type[Matcher],
    event: MessageEvent,
    args: Message,
    *,
    use_web: bool,
) -> None:
    await _enforce_group_access(
        matcher,
        event,
        feature="imagen",
        display_name="生图",
    )

    error_prefix = "联网生图失败" if use_web else "生图失败"
    log_context = "Draw Web Error" if use_web else "Draw Error"

    try:
        content_list = await parse_message_content(
            event,
            args,
            image_purpose="generation",
        )
        if not content_list:
            await matcher.finish("请提供文字描述，或回复一张图片。")

        context_text = None
        if use_web:
            raw_text = extract_pure_text(content_list).strip()
            if not raw_text:
                await matcher.finish("未检测到可用于搜索的文本内容。")

            await matcher.send("正在联网搜索视觉设定中...")
            queries, search_payloads = await web_image_search_with_rewrite(raw_text)

            await matcher.send("正在提炼视觉设定...")
            visual_brief = await build_visual_brief_from_search(
                raw_text,
                queries,
                search_payloads,
            )
            context_text = compile_image_prompt_from_visual_brief(
                raw_text,
                visual_brief,
            )

        await matcher.send("正在绘制中，请稍候...")

        started_at = time.perf_counter()
        image_url = await call_image_generation(
            content_list,
            extra_context=context_text,
        )
        generation_elapsed = time.perf_counter() - started_at

        started_at = time.perf_counter()
        await matcher.send(MessageSegment.image(image_url))
        send_elapsed = time.perf_counter() - started_at

        stat_text = (
            f"使用模型：{plugin_config.image.model}\n"
            f"生成耗费{generation_elapsed:.2f}s，发送耗费{send_elapsed:.2f}s"
        )
        if use_web:
            stat_text += "\n联网：Tavily，已提炼视觉设定"
        await matcher.finish(stat_text)

    except FinishedException:
        raise
    except Exception as exc:
        logger.exception(log_context)
        await matcher.finish(f"{error_prefix}: {exc}")


@chat_matcher.handle()
async def handle_chat(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    await _handle_chat_command(
        chat_matcher,
        bot,
        event,
        args,
        force_search=False,
    )


@chat_web_matcher.handle()
async def handle_chat_web(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
):
    await _handle_chat_command(
        chat_web_matcher,
        bot,
        event,
        args,
        force_search=True,
    )


@draw_matcher.handle()
async def handle_draw(event: MessageEvent, args: Message = CommandArg()):
    await _handle_draw_command(
        draw_matcher,
        event,
        args,
        use_web=False,
    )


@draw_web_matcher.handle()
async def handle_draw_web(event: MessageEvent, args: Message = CommandArg()):
    await _handle_draw_command(
        draw_web_matcher,
        event,
        args,
        use_web=True,
    )


@model_cmd.handle()
async def handle_change_model(args: Message = CommandArg()):
    new_model = args.extract_plain_text().strip()
    if not new_model:
        await model_cmd.finish("请提供新的模型名称。例如：切换模型 gpt-4")
    
    old_model = plugin_config.chat.model
    if old_model == new_model:
        await model_cmd.finish(f"当前已经是 {new_model} 模型了。")

    await model_cmd.send(f"正在尝试切换到模型: {new_model}\n正在进行连接测试，请稍候...")
    
    # 临时修改配置
    plugin_config.chat.model = new_model
    
    try:
        # 构造测试消息
        messages = [{"role": "user", "content": "Hello! This is a connection test."}]
        
        # 发起测试请求
        result = await call_chat_completion(messages)
        reply_text = result.content
        used_model = result.model

        # 如果代码执行到这里，说明测试成功
        save_config(plugin_config)

        # 截取简短的响应预览
        preview = reply_text[:50] + "..." if len(reply_text) > 50 else reply_text
        preview = preview.replace('\n', ' ')

        await model_cmd.finish(
            f"✅ 模型切换成功！\n"
            f"旧模型: {old_model}\n"
            f"新模型: {used_model}\n"
            f"测试响应: {preview}"
        )
    
    except FinishedException:
        raise

    except Exception as e:
        # 测试失败，回滚配置
        plugin_config.chat.model = old_model
        
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
            
        await model_cmd.finish(
            f"❌ 切换失败，模型 {new_model} 似乎不可用。\n"
            f"已回滚到: {old_model}\n"
            f"错误信息: {error_msg}"
        )
