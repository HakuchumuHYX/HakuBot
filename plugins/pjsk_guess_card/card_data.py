"""卡牌数据加载与管理模块"""
import json
import random
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple
from nonebot.log import logger

from .config import plugin_config


# 可用卡牌列表（已过滤）
_available_cards: List[Dict] = []

CardImageType = Literal["normal", "after_training"]

# masterdata 路径
MASTERDATA_DIR = Path(__file__).parent.parent.parent.parent / "haruki-sekai-master" / "master"


def _has_after_training(card: Dict) -> bool:
    """判断卡牌是否有特训后卡面（3星、4星有）"""
    return card["cardRarityType"] in ("rarity_3", "rarity_4")


def get_card_image_types(card: Dict) -> Tuple[CardImageType, ...]:
    """获取卡牌可用的卡图类型。"""
    if _has_after_training(card):
        return ("normal", "after_training")
    return ("normal",)


def load_cards():
    """从 masterdata 加载卡牌数据，过滤出可用的卡牌"""
    global _available_cards
    _available_cards.clear()

    masterdata_path = plugin_config.masterdata_path
    if masterdata_path:
        cards_file = Path(masterdata_path) / "cards.json"
    else:
        cards_file = MASTERDATA_DIR / "cards.json"

    try:
        with open(cards_file, "r", encoding="utf-8") as f:
            all_cards = json.load(f)

        now_ts = datetime.now().timestamp() * 1000
        for card in all_cards:
            # 只保留已发布的 3星/4星/生日卡
            if card["cardRarityType"] not in ("rarity_3", "rarity_4", "rarity_birthday"):
                continue
            if card["releaseAt"] > now_ts:
                continue
            _available_cards.append(card)

        logger.info(f"已加载 {len(_available_cards)} 张可用卡牌（3星/4星/生日卡）")
    except Exception as e:
        logger.error(f"加载卡牌数据失败: {e}")


def random_card() -> Tuple[Dict, CardImageType]:
    """
    随机选一张卡牌，返回 (卡牌数据, 卡图类型)
    """
    if not _available_cards:
        raise RuntimeError("没有可用的卡牌数据，请检查 masterdata 路径")

    card = random.choice(_available_cards)
    image_type = random.choice(get_card_image_types(card))
    return card, image_type


def get_card_image_url(card: Dict, image_type: CardImageType) -> str:
    """
    拼接卡面图片的完整下载 URL
    格式: {base_url}character/member/{assetbundleName}/card_{normal|after_training}.png
    """
    base_url = plugin_config.asset_base_url.rstrip("/") + "/"
    asset_name = card["assetbundleName"]
    return f"{base_url}character/member/{asset_name}/card_{image_type}.png"


def get_card_title(card: Dict, image_type: CardImageType) -> str:
    """获取卡面的显示标题"""
    from .nickname import get_character_name_by_id

    title = f"【{card['id']}】"
    rarity = card["cardRarityType"]
    if rarity == "rarity_3":
        title += "⭐⭐⭐"
    elif rarity == "rarity_4":
        title += "⭐⭐⭐⭐"
    elif rarity == "rarity_birthday":
        title += "🎀"

    title += " " + get_character_name_by_id(card["characterId"])
    title += f" - {card['prefix']}"

    if rarity in ("rarity_3", "rarity_4"):
        title += "（特训后）" if image_type == "after_training" else "（特训前）"

    return title


def get_card_hint(card: Dict, used_hints: set) -> Optional[str]:
    """
    获取一个未使用过的提示，返回提示文本。
    如果没有更多提示，返回 None。
    """
    from .nickname import get_character_name_by_id

    # 角色ID -> 团名映射
    CID_UNIT_MAP = {
        1: "ln", 2: "ln", 3: "ln", 4: "ln",
        5: "mmj", 6: "mmj", 7: "mmj", 8: "mmj",
        9: "vbs", 10: "vbs", 11: "vbs", 12: "vbs",
        13: "ws", 14: "ws", 15: "ws", 16: "ws",
        17: "25时", 18: "25时", 19: "25时", 20: "25时",
        21: "vs", 22: "vs", 23: "vs", 24: "vs", 25: "vs", 26: "vs",
    }

    hint_types = ["title", "rarity_and_attr", "unit"]
    available = [h for h in hint_types if h not in used_hints]
    if not available:
        return None

    hint = random.choice(available)
    used_hints.add(hint)

    if hint == "title":
        return f"提示：标题为「{card['prefix']}」"
    elif hint == "rarity_and_attr":
        rarity = card["cardRarityType"]
        rarity_text = {"rarity_3": "3星", "rarity_4": "4星", "rarity_birthday": "生日卡"}.get(rarity, "?")
        attr = card.get("attr", "")
        attr_text = {
            "cool": "蓝星", "happy": "橙心", "mysterious": "紫月",
            "cute": "粉花", "pure": "绿草"
        }.get(attr, attr)
        return f"提示：{rarity_text} & {attr_text}"
    elif hint == "unit":
        unit = CID_UNIT_MAP.get(card["characterId"], "?")
        return f"提示：所属团为 {unit}"

    return None
