from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.exception import FinishedException

from .utils import *
from .service import call_chat_completion, call_image_generation

try:
    from ..plugin_manager.enable import is_plugin_enabled, is_feature_enabled
    from ..plugin_manager.cd_manager import check_cd, update_cd

    MANAGER_AVAILABLE = True
except ImportError:
    logger.warning("未找到 plugin_manager 插件，将跳过管理功能检查。")
    MANAGER_AVAILABLE = False

PLUGIN_NAME = "ai_assistant"

# 注册命令
chat_matcher = on_command("chat", priority=5, block=True)
draw_matcher = on_command("生图", priority=5, block=True)


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
            {"role": "system", "content": "你是一个有用的AI助手。"},
            {"role": "user", "content": content_list}
        ]

        await chat_matcher.send("正在思考中...")
        reply_text, model_name, tokens = await call_chat_completion(messages)

        cleaned_text = remove_markdown(reply_text)
        final_msg = f"{cleaned_text}\n\n—— 使用模型: {model_name} | Token消耗: {tokens}"

        await chat_matcher.finish(final_msg)

    except FinishedException:
        raise

    except Exception as e:
        logger.error(f"Chat Error: {e}")
        await chat_matcher.finish(f"发生错误: {str(e)}")


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

        image_url = await call_image_generation(content_list)

        await draw_matcher.finish(MessageSegment.image(image_url))

    except FinishedException:
        raise

    except Exception as e:
        logger.exception("Draw Error")
        await draw_matcher.finish(f"生图失败: {str(e)}")
