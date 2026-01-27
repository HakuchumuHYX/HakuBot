import json
import random
import re
from pathlib import Path
from typing import Optional, Tuple

from nonebot import get_driver, on_message, logger
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.exception import FinishedException
from nonebot.rule import Rule

from ..plugin_manager.enable import is_plugin_enabled

# 配置文件路径
CONFIG_FILE = Path(__file__).parent / "config.json"

# 全局配置
plugin_config = {
    "keywords": [],  # 已弃用：不再以关键词触发（保留字段以兼容旧配置结构）
    "responses": [],
    "error_responses": [],
}


def load_config():
    """加载配置文件"""
    global plugin_config
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                plugin_config["keywords"] = config_data.get("keywords", [])
                plugin_config["responses"] = config_data.get("responses", [])
                plugin_config["error_responses"] = config_data.get("error_responses", [])
            logger.info(
                f"二择插件配置加载成功，关键词: {len(plugin_config['keywords'])} 个，回复模板: {len(plugin_config['responses'])} 个"
            )
        else:
            # 如果配置文件不存在，创建默认配置
            default_config = {
                "keywords": [],
                "responses": [
                    "我建议选择：{choice}",
                    "我觉得 {choice} 更好一些",
                    "要不试试 {choice}？",
                    "我推荐 {choice}",
                    "{choice} 看起来不错",
                ],
                "error_responses": [
                    "我没看懂你的二择格式呢，请用 1d 开头：1d吃不吃午饭 / 1d抽这个还是那个"
                ],
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            plugin_config = default_config
            logger.info("创建默认配置文件成功")
    except Exception as e:
        logger.error(f"加载配置文件失败: {e}")


def _strip_1d_prefix(text: str) -> Optional[str]:
    """
    若文本以 1d/1D 开头，则去掉前缀并返回剩余内容；否则返回 None。
    """
    raw = text.strip()
    if not re.match(r"^1d", raw, flags=re.IGNORECASE):
        return None
    body = re.sub(r"^1d\s*", "", raw, flags=re.IGNORECASE).strip()
    return body if body else ""


def _extract_by_separator(body: str) -> Optional[Tuple[str, str]]:
    """
    分隔符切分：仅用于解析，不用于触发。
    """
    # 注意：这里的“还是/或者”等不再作为触发关键词，只在 1d 模式下用于拆分选项
    separators = [
        "还是说",
        "或者说是",
        "抑或是",
        "亦或是",
        "或者",
        "还是",
        "抑或",
        "亦或",
        "or",
        "|",
        "/",
        "或",
    ]

    # 优先匹配更长的分隔词
    sep_pattern = "|".join(re.escape(s) for s in sorted(separators, key=len, reverse=True))
    pattern = rf"(.+?)\s*(?:{sep_pattern})\s*(.+)"
    match = re.search(pattern, body, flags=re.IGNORECASE)
    if not match:
        return None

    left = match.group(1).strip()
    right = match.group(2).strip()
    if not left or not right:
        return None

    # 长度简单约束，避免极端误判
    if len(left) > 80 or len(right) > 80:
        return None

    return left, right


def _extract_a_not_a(body: str) -> Optional[Tuple[str, str]]:
    """
    A不A 型：例如
    - 吃不吃午饭 -> 吃午饭 / 不吃午饭
    - 去不去 -> 去 / 不去
    """
    text = body.strip()

    # 形式：A不A + (可选)尾部宾语
    # A 尽量短一点，避免贪婪吞掉宾语；同时允许尾部为空
    m = re.match(r"^(.{1,10}?)不\1(.*)$", text)
    if not m:
        return None

    a = m.group(1).strip()
    tail = (m.group(2) or "").strip()

    if not a:
        return None

    choice1 = (a + tail).strip()
    choice2 = ("不" + a + tail).strip()

    # 避免产生完全相同的结果
    if choice1 == choice2:
        return None

    # 轻量长度限制
    if len(choice1) > 80 or len(choice2) > 80:
        return None

    return choice1, choice2


def _extract_yes_no_patterns(body: str) -> Optional[Tuple[str, str]]:
    """
    一些常见口语化是/否二择：
    - 要不要X -> 要X / 不要X
    - 能不能X -> 能X / 不能X
    """
    text = body.strip()

    m = re.match(r"^要不要(.+)$", text)
    if m:
        tail = m.group(1).strip()
        return f"要{tail}", f"不要{tail}"

    m = re.match(r"^能不能(.+)$", text)
    if m:
        tail = m.group(1).strip()
        return f"能{tail}", f"不能{tail}"

    m = re.match(r"^可不可以(.+)$", text)
    if m:
        tail = m.group(1).strip()
        return f"可以{tail}", f"不可以{tail}"

    return None


def extract_choices(text: str) -> Optional[Tuple[str, str]]:
    """
    从文本中提取两个选择项（仅支持以 1d 开头的格式）。

    示例：
    - 1d吃不吃午饭 -> 吃午饭 / 不吃午饭
    - 1d抽这个还是那个 -> 抽这个 / 那个
    """
    body = _strip_1d_prefix(text)
    if body is None:
        return None

    # 解析优先级：分隔符 > A不A > 口语化是/否
    for fn in (_extract_by_separator, _extract_a_not_a, _extract_yes_no_patterns):
        res = fn(body)
        if res:
            return res

    return None


def is_two_choice_message(event: MessageEvent) -> bool:
    """
    检查消息是否为二择问题：
    - 群聊：不需要 @，只要以 1d 开头即可触发
    - 私聊：同样以 1d 开头触发
    """
    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        if not is_plugin_enabled("two_choices", str(event.group_id), user_id):
            return False

    message_text = event.get_plaintext().strip()

    # 仅以 1d 开头触发
    if not re.match(r"^1d", message_text, flags=re.IGNORECASE):
        return False

    # 能解析出两个选项才触发，避免 1d 单独发触发
    return extract_choices(message_text) is not None


# 创建消息处理器
two_choice = on_message(rule=Rule(is_two_choice_message), priority=10, block=True)


@two_choice.handle()
async def handle_two_choice(event: MessageEvent):
    """处理二择问题"""
    try:
        message_text = event.get_plaintext().strip()
        choices = extract_choices(message_text)

        if not choices:
            if plugin_config["error_responses"]:
                await two_choice.finish(random.choice(plugin_config["error_responses"]))
            return

        choice1, choice2 = choices

        selected_choice = random.choice([choice1, choice2])

        if plugin_config["responses"]:
            response_template = random.choice(plugin_config["responses"])
            reply_text = response_template.format(choice=selected_choice)
            await two_choice.finish(reply_text)
        else:
            await two_choice.finish(f"我建议选择：{selected_choice}")

    except FinishedException:
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
        f"关键词示例: {', '.join(plugin_config['keywords'][:5]) if plugin_config['keywords'] else '(空)'}"
    )
    await view_config.finish(config_info)
