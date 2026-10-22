"""卡池：启动时从 masterdata 建卡池，加上角色全名、所属团和昵称；答案标题、提示、随机裁剪"""

import json
import random
import time

from PIL import Image

from plugins.pjsk_guess_card.config import MASTER_DIR, NICKNAMES_FILE, plugin_config

RARITY_STARS = {"rarity_3": "⭐⭐⭐", "rarity_4": "⭐⭐⭐⭐", "rarity_birthday": "🎀"}
RARITY_TEXT = {"rarity_3": "3星", "rarity_4": "4星", "rarity_birthday": "生日卡"}
# 只有 3 星、4 星有特训后卡面
TRAINABLE = ("rarity_3", "rarity_4")
ATTR_TEXT = {
    "cool": "蓝星",
    "happy": "橙心",
    "mysterious": "紫月",
    "cute": "粉花",
    "pure": "绿草",
}
UNIT_NAMES = {
    "light_sound": "ln",
    "idol": "mmj",
    "street": "vbs",
    "theme_park": "ws",
    "school_refusal": "25时",
    "piapro": "vs",
}
HINT_TYPES = ("title", "rarity_and_attr", "unit")

_cards: list[dict] = []
_full_names: dict[int, str] = {}
_units: dict[int, str] = {}
# 昵称（小写）→ 角色 ID
_nicknames: dict[str, int] = {}


def _read_master(name: str) -> list[dict]:
    return json.loads((MASTER_DIR / name).read_text(encoding="utf-8"))


def load() -> None:
    now_ms = time.time() * 1000
    _cards[:] = [
        card
        for card in _read_master("cards.json")
        if card["cardRarityType"] in RARITY_STARS and card["releaseAt"] <= now_ms
    ]

    characters = _read_master("gameCharacters.json")
    _full_names.clear()
    _full_names.update(
        {c["id"]: (c.get("firstName") or "") + c["givenName"] for c in characters}
    )
    _units.clear()
    _units.update({c["id"]: c["unit"] for c in characters})

    _nicknames.clear()
    for item in json.loads(NICKNAMES_FILE.read_text(encoding="utf-8")):
        for nickname in item["nicknames"]:
            _nicknames[nickname.lower()] = item["id"]


def count() -> int:
    return len(_cards)


def random_card(character_id: int | None = None) -> tuple[dict, str]:
    """随机一张卡（可限定角色），返回 (卡牌数据, 卡图类型 normal / after_training)"""
    pool = _cards
    if character_id is not None:
        pool = [c for c in _cards if c["characterId"] == character_id]
    card = random.choice(pool)
    if card["cardRarityType"] in TRAINABLE:
        return card, random.choice(("normal", "after_training"))
    return card, "normal"


def image_url(card: dict, image_type: str) -> str:
    base = plugin_config.asset_base_url.rstrip("/")
    return f"{base}/character/member/{card['assetbundleName']}/card_{image_type}.png"


def character_by_nickname(text: str) -> int | None:
    return _nicknames.get(text.lower())


def title(card: dict, image_type: str) -> str:
    rarity = card["cardRarityType"]
    text = (
        f"【{card['id']}】{RARITY_STARS[rarity]} "
        f"{_full_names[card['characterId']]} - {card['prefix']}"
    )
    if rarity in TRAINABLE:
        text += "（特训后）" if image_type == "after_training" else "（特训前）"
    return text


def next_hint(card: dict, used: set[str]) -> str | None:
    """随机给一条还没给过的提示，给完了返回 None"""
    available = [h for h in HINT_TYPES if h not in used]
    if not available:
        return None
    hint = random.choice(available)
    used.add(hint)

    if hint == "title":
        return f"提示：标题为「{card['prefix']}」"
    if hint == "rarity_and_attr":
        attr = ATTR_TEXT.get(card["attr"], card["attr"])
        return f"提示：{RARITY_TEXT[card['cardRarityType']]} & {attr}"
    return f"提示：所属团为 {UNIT_NAMES[_units[card['characterId']]]}"


def crop(image: Image.Image) -> Image.Image:
    """随机裁下一小块，宽高各占原图的 crop_rate_min ~ crop_rate_max"""
    rates = (plugin_config.crop_rate_min, plugin_config.crop_rate_max)
    w, h = image.size
    w_crop = int(w * random.uniform(*rates))
    h_crop = int(h * random.uniform(*rates))
    x = random.randint(0, w - w_crop)
    y = random.randint(0, h - h_crop)
    return image.crop((x, y, x + w_crop, y + h_crop))
