import json
import random
import re
from pathlib import Path
from typing import List, Tuple, Optional

from nonebot import get_driver, on_message, logger
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.rule import Rule
from nonebot.exception import FinishedException
from ..plugin_manager.enable import is_plugin_enabled

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"

# 全局配置
plugin_config = {
    "keywords": [],
    "responses": [],
    "error_responses": []
}


def load_config():
    """加载配置文件"""
    global plugin_config
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                plugin_config["keywords"] = config_data.get("keywords", [])
                plugin_config["responses"] = config_data.get("responses", [])
                plugin_config["error_responses"] = config_data.get("error_responses", [])
            logger.info(
                f"二择插件配置加载成功，关键词: {len(plugin_config['keywords'])} 个，回复模板: {len(plugin_config['responses'])} 个")
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {
                "keywords": [
                    " or ",
                    " 还是 ",
                    " 或者 ",
                    " 抑或是 ",
                    " 或许是 ",
                    " 可能是 ",
                    " 要不 ",
                    " 还是说 ",
                    " 亦或是 ",
                    " 要不然 ",
                    " 或 ",
                    " 或者说是 ",
                    " 或者还是 ",
                    " 要么 ",
                    " 要不就 "
                ],
                "responses": [
                    "我建议选择：{choice}",
                    "我觉得 {choice} 更好一些",
                    "要不试试 {choice}？",
                    "我推荐 {choice}",
                    "{choice} 看起来不错"
                ],
                "error_responses": [
                    "我没看懂你的选择呢，格式如：吃饭 or 睡觉",
                    "请用'还是'、'或者'之类的词连接两个选项哦"
                ]
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            plugin_config = default_config
            logger.info("创建默认配置文件成功")
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")


def extract_choices(text: str) -> Optional[Tuple[str, str]]:
    """
    从文本中提取两个选择项

    Args:
        text: 用户输入的文本

    Returns:
        Tuple[选项1, 选项2] 或 None
    """
    # 预处理文本
    text = text.strip()

    # 遍历所有关键词进行匹配
    for keyword in plugin_config["keywords"]:
        # 去掉关键词前后的空格用于正则匹配
        clean_keyword = keyword.strip()

        # 构建正则表达式，考虑中英文括号和空格
        # 匹配模式：任意字符 + 关键词 + 任意字符
        pattern = rf'(.+?)\s*{re.escape(clean_keyword)}\s*(.+)'

        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            choice1 = match.group(1).strip()
            choice2 = match.group(2).strip()

            # 检查选项是否有效（非空且长度合理）
            if choice1 and choice2 and len(choice1) < 50 and len(choice2) < 50:
                return choice1, choice2

    return None


def is_two_choice_message(event: MessageEvent) -> bool:
    """检查消息是否为二择问题且@了机器人"""
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("two_choice", str(event.group_id)):
            return False
    # 获取纯文本内容
    message_text = event.get_plaintext().strip()

    # 检查是否包含二择关键词
    has_keyword = False
    for keyword in plugin_config["keywords"]:
        clean_keyword = keyword.strip()
        if clean_keyword in message_text:
            choices = extract_choices(message_text)
            if choices:
                has_keyword = True
                break

    if not has_keyword:
        return False

    # 对于群消息，必须明确@机器人
    if isinstance(event, GroupMessageEvent):
        # 严格检查：消息中必须包含@机器人的消息段
        has_at_bot = False
        for segment in event.original_message:
            if segment.type == "at":
                at_qq = segment.data.get("qq", "")
                # 检查是否@了当前机器人
                if at_qq == str(event.self_id):
                    has_at_bot = True
                    break

        # 只有明确@了机器人并且有二择关键词才触发
        return has_at_bot
    else:
        # 私聊消息直接处理
        return True


# 创建消息处理器
two_choice = on_message(rule=Rule(is_two_choice_message), priority=10, block=True)


@two_choice.handle()
async def handle_two_choice(event: MessageEvent):
    """处理二择问题"""
    try:
        # 获取纯文本内容
        message_text = event.get_plaintext().strip()

        choices = extract_choices(message_text)

        if not choices:
            # 如果提取失败，发送错误提示
            if plugin_config["error_responses"]:
                error_msg = random.choice(plugin_config["error_responses"])
                await two_choice.finish(error_msg)
            return

        choice1, choice2 = choices

        # 随机选择一个选项
        selected_choice = random.choice([choice1, choice2])

        # 随机选择一个回复模板
        if plugin_config["responses"]:
            response_template = random.choice(plugin_config["responses"])
            reply_text = response_template.format(choice=selected_choice)
            await two_choice.finish(reply_text)
        else:
            await two_choice.finish(f"我建议选择：{selected_choice}")

    except FinishedException:
        # 忽略 FinishedException，这是正常的结束流程
        raise
    except Exception as e:
        logger.error(f"处理二择问题时出错: {e}")
        await two_choice.finish("出现了一些问题，请稍后再试~")


# 插件启动时加载配置
@get_driver().on_startup
async def init_plugin():
    """插件初始化"""
    load_config()
    logger.info("二择插件初始化完成")


# 重新加载配置的命令
from nonebot import on_command
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me

reload_config = on_command("重载二择配置", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


@reload_config.handle()
async def handle_reload_config():
    """重载配置文件"""
    load_config()
    await reload_config.finish("二择插件配置重载成功！")


# 查看当前配置的命令
view_config = on_command("查看二择配置", permission=SUPERUSER, rule=to_me(), priority=5, block=True)


@view_config.handle()
async def handle_view_config():
    """查看当前配置"""
    config_info = (
        f"关键词数量: {len(plugin_config['keywords'])}\n"
        f"回复模板数量: {len(plugin_config['responses'])}\n"
        f"错误回复数量: {len(plugin_config['error_responses'])}\n\n"
        f"关键词示例: {', '.join(plugin_config['keywords'][:5])}..."
    )
    await view_config.finish(config_info)