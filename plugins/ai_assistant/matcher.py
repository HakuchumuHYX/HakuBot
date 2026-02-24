import time
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment, GroupMessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException
from nonebot import require, get_driver
import aiofiles
import os
from pathlib import Path

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import md_to_pic
from nonebot_plugin_htmlrender.data_source import read_tpl

from .utils import *
from .config import plugin_config, save_config
from .services.chat_service import call_chat_completion
from .services.imagen_service import call_image_generation
from .services.search_service import format_search_results, web_search_with_rewrite
from .services.music_service import get_music_service

try:
    from ..plugin_manager.enable import is_plugin_enabled, is_feature_enabled
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

# AI 点歌（自然语言）
music_matcher = on_command("点歌", priority=4, block=True)

model_cmd = on_command("切换模型", aliases={"更改模型", "change_model"}, permission=SUPERUSER, priority=1, block=True)


@music_matcher.handle()
async def handle_music(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    AI 点歌：自然语言 -> LLM(可选联网) -> 播放
    用法：
      点歌 <自然语言描述>
      也支持回复一条消息后直接“点歌”
    """
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1) 插件总开关
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await music_matcher.finish()

        # 2) 功能分开关 (feature: music)
        if not is_feature_enabled(PLUGIN_NAME, "music", group_id, user_id):
            await music_matcher.finish()

        # 3) 功能 CD (key: ai_assistant:music)
        cd_key = f"{PLUGIN_NAME}:music"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await music_matcher.finish(f"点歌功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        # 4) 更新 CD（命令成功触发即进入CD，避免刷）
        update_cd(cd_key, group_id, user_id)

    try:
        content_list = await parse_message_content(event, args)
        raw_text = extract_pure_text(content_list).strip()

        if not raw_text:
            await music_matcher.finish("请在命令后描述你想听的歌，例如：点歌 给我随便放一首陶喆的歌")

        svc = get_music_service()
        result = await svc.play_by_natural_language(bot, event, raw_text)

        # Now Playing 已在 service 内部提前 send 过，若 result 为空则不再额外输出
        if result:
            await music_matcher.finish(result)
        else:
            await music_matcher.finish()

    except FinishedException:
        raise
    except Exception as e:
        logger.exception("AI Music Error")
        await music_matcher.finish(f"点歌失败: {str(e)}")


@chat_matcher.handle()
async def handle_chat(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 检查插件总开关
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await chat_matcher.finish()  # 禁用时静默失败

        # 2. 检查功能分开关 (feature: chat)
        if not is_feature_enabled(PLUGIN_NAME, "chat", group_id, user_id):
            await chat_matcher.finish()

        # 3. 检查功能 CD (key: ai_assistant:chat)
        cd_key = f"{PLUGIN_NAME}:chat"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await chat_matcher.finish(f"Chat功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        # 4. 更新 CD (命令成功触发即进入CD)
        update_cd(cd_key, group_id, user_id)

    try:
        content_list = await parse_message_content(event, args)

        if not content_list:
            await chat_matcher.finish("请提供对话内容，或回复包含内容的消息。")

        messages = [
            {"role": "system", "content": plugin_config.chat.system_prompt},
            {"role": "user", "content": content_list}
        ]

        await chat_matcher.send("正在思考中...")
        reply_text, model_name, tokens = await call_chat_completion(
            messages,
            temperature=plugin_config.chat.temperature,
            top_p=plugin_config.chat.top_p,
            assistant_prefill=plugin_config.chat.assistant_prefill,
        )

        stat_text = f"—— 使用模型: {model_name} | Token消耗: {tokens}"
        watermark = plugin_config.chat.watermark

        if watermark:
            watermark_html = watermark.replace('\n', '<br>')
            md_content = reply_text + f"\n\n---\n*{stat_text}*\n\n<div align='right' style='color: gray; font-size: 0.9em; font-style: italic;'>{watermark_html}</div>"
        else:
            md_content = reply_text + f"\n\n---\n*{stat_text}*"
        
        try:
            css_path = str(CUSTOM_CSS_PATH.absolute()) if CUSTOM_CSS_PATH.exists() else ""
            img_bytes = await md_to_pic(md=md_content, width=800, css_path=css_path)
            reply_msg = MessageSegment.image(img_bytes)
        except Exception as e:
            logger.error(f"渲染 Markdown 失败: {e}")
            cleaned_text = remove_markdown(reply_text)
            reply_msg = Message(cleaned_text + f"\n\n{stat_text}")

        await chat_matcher.finish(reply_msg, at_sender=True)

    except FinishedException:
        raise

    except Exception as e:
        logger.error(f"Chat Error: {e}")
        await chat_matcher.finish(f"发生错误: {str(e)}")


@chat_web_matcher.handle()
async def handle_chat_web(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    手动联网 Chat：先 Tavily 搜索，再将搜索结果注入 prompt，让模型基于最新资料回答。
    """
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 检查插件总开关
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await chat_web_matcher.finish()

        # 2. 检查功能分开关 (feature: chat)
        if not is_feature_enabled(PLUGIN_NAME, "chat", group_id, user_id):
            await chat_web_matcher.finish()

        # 3. 检查功能 CD (key: ai_assistant:chat)
        cd_key = f"{PLUGIN_NAME}:chat"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await chat_web_matcher.finish(f"Chat功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        # 4. 更新 CD
        update_cd(cd_key, group_id, user_id)

    try:
        content_list = await parse_message_content(event, args)
        if not content_list:
            await chat_web_matcher.finish("请提供对话内容，或回复包含内容的消息。")

        raw_text = extract_pure_text(content_list).strip()
        if not raw_text:
            await chat_web_matcher.finish("未检测到可用于搜索的文本内容。")

        await chat_web_matcher.send("正在联网搜索中...")
        queries, results = await web_search_with_rewrite(raw_text, mode="chat")
        context_text = format_search_results(results)

        queries_hint = " / ".join(queries) if queries else "（无）"

        messages = [
            {"role": "system", "content": plugin_config.chat.system_prompt},
            {
                "role": "system",
                "content": (
                    "你将收到一段【联网搜索结果】（含编号与链接）以及【本次检索 query】。请严格遵守以下规则：\n"
                    "1) 事实性结论（数据/时间/版本/政策/新闻等）必须来自【联网搜索结果】并附引用编号，例如：[1] 或 [1][2]。\n"
                    "2) 允许补充少量通用解释（不属于最新事实），但不得杜撰搜索结果中不存在的来源/链接。\n"
                    "3) 禁止编造来源：不要生成搜索结果里不存在的 URL/标题/编号。\n"
                    "4) 如果搜索结果没有覆盖问题关键点：请明确说明“搜索结果未包含X”，并给出建议的补充检索关键词。\n"
                    "5) 如果不同来源有冲突：请指出冲突，并说明你倾向哪一条（可依据更新时间/权威性）。\n"
                    "6) 输出结构：先给结论与解释（带引用编号），最后单独列出“来源：”并逐条贴出对应 URL。\n"
                    "\n【本次检索 query】\n" + queries_hint +
                    "\n\n【联网搜索结果】\n" + context_text
                ),
            },
            {"role": "user", "content": content_list},
        ]

        await chat_web_matcher.send("正在思考中...")
        reply_text, model_name, tokens = await call_chat_completion(
            messages,
            temperature=plugin_config.chat.temperature,
            top_p=plugin_config.chat.top_p,
            assistant_prefill=plugin_config.chat.assistant_prefill,
        )

        stat_text = f"—— 使用模型: {model_name} | Token消耗: {tokens} | 联网: Tavily | Query数: {len(queries) if queries else 0}"
        watermark = plugin_config.chat.watermark

        if watermark:
            watermark_html = watermark.replace('\n', '<br>')
            md_content = reply_text + f"\n\n---\n*{stat_text}*\n\n<div align='right' style='color: gray; font-size: 0.9em; font-style: italic;'>{watermark_html}</div>"
        else:
            md_content = reply_text + f"\n\n---\n*{stat_text}*"
        
        try:
            css_path = str(CUSTOM_CSS_PATH.absolute()) if CUSTOM_CSS_PATH.exists() else ""
            img_bytes = await md_to_pic(md=md_content, width=800, css_path=css_path)
            reply_msg = MessageSegment.image(img_bytes)
        except Exception as e:
            logger.error(f"渲染 Markdown 失败: {e}")
            cleaned_text = remove_markdown(reply_text)
            reply_msg = Message(cleaned_text + f"\n\n{stat_text}")

        await chat_web_matcher.finish(reply_msg, at_sender=True)

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Chat Web Error: {e}")
        await chat_web_matcher.finish(f"联网Chat失败: {str(e)}")


@draw_matcher.handle()
async def handle_draw(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 检查插件总开关
        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await draw_matcher.finish()

        # 2. 检查功能分开关 (feature: imagen)
        if not is_feature_enabled(PLUGIN_NAME, "imagen", group_id, user_id):
            await draw_matcher.finish()

        # 3. 检查功能 CD (key: ai_assistant:imagen)
        cd_key = f"{PLUGIN_NAME}:imagen"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await draw_matcher.finish(f"生图功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        # 4. 更新 CD
        update_cd(cd_key, group_id, user_id)

    try:
        content_list = await parse_message_content(event, args)

        if not content_list:
            await draw_matcher.finish("请提供文字描述，或回复一张图片。")

        await draw_matcher.send("正在绘制中，请稍候...")

        t1 = time.time()
        image_url, meta = await call_image_generation(content_list)
        t2 = time.time()
        gen_time = t2 - t1

        t3 = time.time()
        try:
            await draw_matcher.send(MessageSegment.image(image_url))
        except ActionFailed as e:
            # OneBot 端无法下载远端图片（鉴权/签名/防盗链等），改为机器人侧下载并以 base64 发送
            logger.warning(f"OneBot 下载图片失败，尝试 base64 发送。retcode={getattr(e, 'retcode', None)} wording={getattr(e, 'wording', None)} url={image_url}")
            b64_payload = await download_image_as_onebot_base64(image_url)
            await draw_matcher.send(MessageSegment.image(b64_payload))
        t4 = time.time()
        send_time = t4 - t3

        note = ""
        if isinstance(meta, dict) and meta.get("used_safe_rewrite"):
            # “触发安全重写”不准确：实际是发生了合规化改写并重试
            note = "（已进行合规化改写并重试）"

        await draw_matcher.finish(f"生成耗费{gen_time:.2f}s，发送耗费{send_time:.2f}s{note}")

    except FinishedException:
        raise

    except Exception as e:
        logger.exception("Draw Error")
        await draw_matcher.finish(f"生图失败: {str(e)}")


@draw_web_matcher.handle()
async def handle_draw_web(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """
    手动联网生图：先 Tavily 搜索（补充设定/资料），再将搜索摘要注入 system_instruction 进行生图。
    """
    if MANAGER_AVAILABLE and isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        if not is_plugin_enabled(PLUGIN_NAME, group_id, user_id):
            await draw_web_matcher.finish()

        if not is_feature_enabled(PLUGIN_NAME, "imagen", group_id, user_id):
            await draw_web_matcher.finish()

        cd_key = f"{PLUGIN_NAME}:imagen"
        cd_remain = check_cd(cd_key, group_id, user_id)
        if cd_remain > 0:
            await draw_web_matcher.finish(f"生图功能冷却中，请等待 {cd_remain} 秒", at_sender=True)

        update_cd(cd_key, group_id, user_id)

    try:
        content_list = await parse_message_content(event, args)
        if not content_list:
            await draw_web_matcher.finish("请提供文字描述，或回复一张图片。")

        raw_text = extract_pure_text(content_list).strip()
        if not raw_text:
            await draw_web_matcher.finish("未检测到可用于搜索的文本内容。")

        await draw_web_matcher.send("正在联网搜索设定/资料中...")
        queries, results = await web_search_with_rewrite(raw_text, mode="image")
        context_text = format_search_results(results, max_chars=1200)

        await draw_web_matcher.send("正在绘制中，请稍候...")

        t1 = time.time()
        image_url, meta = await call_image_generation(content_list, extra_context=context_text)
        t2 = time.time()
        gen_time = t2 - t1

        t3 = time.time()
        try:
            await draw_web_matcher.send(MessageSegment.image(image_url))
        except ActionFailed as e:
            logger.warning(f"OneBot 下载图片失败，尝试 base64 发送。retcode={getattr(e, 'retcode', None)} wording={getattr(e, 'wording', None)} url={image_url}")
            b64_payload = await download_image_as_onebot_base64(image_url)
            await draw_web_matcher.send(MessageSegment.image(b64_payload))
        t4 = time.time()
        send_time = t4 - t3

        note = ""
        if isinstance(meta, dict) and meta.get("used_safe_rewrite"):
            note = "（已进行合规化改写并重试）"

        await draw_web_matcher.finish(f"生成耗费{gen_time:.2f}s，发送耗费{send_time:.2f}s（联网: Tavily）{note}")

    except FinishedException:
        raise
    except Exception as e:
        logger.exception("Draw Web Error")
        await draw_web_matcher.finish(f"联网生图失败: {str(e)}")


@model_cmd.handle()
async def handle_change_model(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
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
        reply_text, used_model, _ = await call_chat_completion(messages)
        
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
