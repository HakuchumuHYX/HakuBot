import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Union
from nonebot import logger

from core.settings import ConfigModel, load_config
from utils.logging import get_exc_desc
from utils.paths import sekai_master_dir
from plugins.pjsk_event_summary.models import (
    Chapter,
    Character,
    EventDetail,
    EventMeta,
    EventSimple,
)

# 由 /opt/scripts/moesekai-auto-pull.sh 维护的 MoeSekai-Hub 稀疏镜像
STORY_DIR = Path("/opt/moesekai-hub/story/detail")
MASTER_DIR = sekai_master_dir() / "master"



class Config(ConfigModel):
    # 游戏资源站基址不进代码，只放在 config.json
    asset_base: str


ASSET_BASE: str = load_config("pjsk_event_summary", Config).asset_base.rstrip("/")

# masterdata 只有日文名
CHAR_CN = {
    1: "星乃一歌", 2: "天马咲希", 3: "望月穗波", 4: "日野森志步",
    5: "花里实乃里", 6: "桐谷遥", 7: "桃井爱莉", 8: "日野森雫",
    9: "小豆泽心羽", 10: "白石杏", 11: "东云彰人", 12: "青柳冬弥",
    13: "天马司", 14: "凤笑梦", 15: "草薙宁宁", 16: "神代类",
    17: "宵崎奏", 18: "朝比奈真冬", 19: "东云绘名", 20: "晓山瑞希",
    21: "初音未来", 22: "镜音铃", 23: "镜音连", 24: "巡音流歌",
    25: "MEIKO", 26: "KAITO",
}
UNIT_NAME = {
    "light_sound": "Leo/need",
    "idol": "MORE MORE JUMP!",
    "street": "Vivid BAD SQUAD",
    "theme_park": "Wonderlands×Showtime",
    "school_refusal": "25時、ナイトコードで。",
    "piapro": "VIRTUAL SINGER",
    "none": "混活",
}
TYPE_NAME = {"marathon": "马拉松", "cheerful_carnival": "5v5", "world_bloom": "World Link"}
MIKU = "#33ccbb"
MIXED_UNIT_COLOR = "#9aa0b4"
CST = timezone(timedelta(hours=8))


def _event_id(path: Path) -> int:
    return int(path.stem.removeprefix("event_"))


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _on_color(hex_: str) -> str:
    """应援色上的文字色：亮底用深字，暗底用白字"""
    r, g, b = (int(hex_[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return "#1b1d2a" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 else "#ffffff"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


class _Master:
    """每次请求现读，masterdata 更新后无需重启；5 张表共约 45ms"""

    def __init__(self):
        self.events = {e["id"]: e for e in _read(MASTER_DIR / "events.json")}
        self.stories = {s["eventId"]: s for s in _read(MASTER_DIR / "eventStories.json")}
        self.char_units = {g["id"]: g for g in _read(MASTER_DIR / "gameCharacterUnits.json")}
        self.unit_color = {u["unit"]: u["colorCode"] for u in _read(MASTER_DIR / "unitProfiles.json")}
        # VIRTUAL SINGER 官方团色是纯白，做主色没法用
        self.unit_color["piapro"] = MIKU
        # 每个角色的本团应援色（id 1-26 是不带支援团的原始组合）
        self.char_color = {
            g["gameCharacterId"]: g["colorCode"] for g in self.char_units.values() if g["id"] <= 26
        }
        self.blooms: dict[int, list[int]] = {}
        for w in _read(MASTER_DIR / "worldBlooms.json"):
            if w.get("gameCharacterId"):
                self.blooms.setdefault(w["eventId"], []).append(w["gameCharacterId"])

    def meta(self, event_id: int) -> EventMeta:
        event = self.events[event_id]
        banner = self.char_units.get(self.stories[event_id].get("bannerGameCharacterUnitId"))
        # World Link 按各章角色、主色取团色（混活用初音色）；其余按 Banner 角色取色
        if event_id in self.blooms:
            char_ids = self.blooms[event_id]
            color = self.unit_color.get(event["unit"], MIKU)
        elif banner:
            char_ids = [banner["gameCharacterId"]]
            color = self.char_color[char_ids[0]]
        else:
            char_ids, color = [], MIKU

        asset = event["assetbundleName"]
        return EventMeta(
            event_id=event_id,
            unit=UNIT_NAME[event["unit"]],
            unit_color=self.unit_color.get(event["unit"], MIXED_UNIT_COLOR),
            chars=[
                Character(
                    name=CHAR_CN[c],
                    color=self.char_color[c],
                    on_color=_on_color(self.char_color[c]),
                )
                for c in char_ids
            ],
            color=color,
            on_color=_on_color(color),
            type=TYPE_NAME[event["eventType"]],
            start=_date(event["startAt"]),
            end=_date(event["aggregateAt"]),
            logo=f"{ASSET_BASE}/event/{asset}/logo/logo.webp",
            # World Link 没有单人立绘
            character="" if event_id in self.blooms else f"{ASSET_BASE}/event/{asset}/screen/character.webp",
        )

    def episode_image(self, event_id: int, chapter_no: int) -> str:
        asset = self.stories[event_id]["assetbundleName"]
        return f"{ASSET_BASE}/event_story/{asset}/episode_image/{asset}_{chapter_no:02d}.webp"


async def fetch_event_list(limit: int = 20) -> List[EventSimple]:
    """
    获取活动列表，按活动 ID 倒序取最新 limit 期
    """
    if not STORY_DIR.exists():
        raise FileNotFoundError(f"MoeSekai-Hub 镜像目录不存在: {STORY_DIR}")

    master = _Master()
    paths = sorted(STORY_DIR.glob("event_*.json"), key=_event_id, reverse=True)
    events = []
    for path in paths[:limit]:
        try:
            data = _read(path)
            events.append(
                EventSimple(
                    **master.meta(_event_id(path)).model_dump(),
                    title_jp=data.get("title_jp", ""),
                    title_cn=data.get("title_cn", ""),
                    chapter_count=len(data.get("chapters") or []),
                )
            )
        except Exception:
            logger.exception(f"读取活动文件失败: {path}")
    return events


async def fetch_event_detail(event_id: str) -> Union[EventDetail, str]:
    """
    获取活动详情
    返回 EventDetail 对象，如果出错或找不到则返回错误信息字符串
    """
    try:
        local_path = STORY_DIR / f"event_{int(event_id):03d}.json"
        if not local_path.exists():
            return f"未收录活动 {event_id} 的剧情总结"

        data = _read(local_path)
        master = _Master()
        eid = int(event_id)
        chapters = [
            Chapter(**{**ch, "image_url": master.episode_image(eid, ch["chapter_no"])})
            for ch in data.pop("chapters")
        ]
        data.pop("event_id")
        return EventDetail(**master.meta(eid).model_dump(), **data, chapters=chapters)
    except Exception as e:
        logger.exception(f"读取活动 {event_id} 详情失败: {e}")
        return f"读取本地剧情数据失败: {get_exc_desc(e)}"
