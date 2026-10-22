"""游戏数据：常量、masterdata 查询、数据包解密与解析"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgpack
from Crypto.Cipher import AES
from nonebot import logger

from utils.logging import get_exc_desc
from utils.paths import sekai_master_dir
from plugins.buaa_msm.config import settings


class MsmError(Exception):
    """用户数据方面的问题，消息直接回复给用户"""


# ============== 常量 ==============

CHARACTER_NAMES: dict[int, str] = {
    1: "星乃 一歌",
    2: "天馬 咲希",
    3: "望月 穂波",
    4: "日野森 志歩",
    5: "花里 みのり",
    6: "桐谷 遥",
    7: "桃井 愛莉",
    8: "日野森 雫",
    9: "小豆沢 こはね",
    10: "白石 杏",
    11: "東雲 彰人",
    12: "青柳 冬弥",
    13: "天馬 司",
    14: "鳳 えむ",
    15: "草薙 寧々",
    16: "神代 類",
    17: "宵崎 奏",
    18: "朝比奈 まふゆ",
    19: "東雲 絵名",
    20: "暁山 瑞希",
    21: "初音 ミク",
    22: "鏡音 リン",
    23: "鏡音 レン",
    24: "巡音 ルカ",
    25: "MEIKO",
    26: "KAITO",
}


@dataclass(frozen=True)
class Site:
    name: str
    image: str  # resources/img 下的底图
    # 底图上的坐标换算：一格的像素数、原点相对底图中心的偏移、轴方向（+1/-1）、是否交换 xy
    grid_px: float
    offset_x: float
    offset_y: float
    x_sign: int
    y_sign: int
    swap_xy: bool


# 有采集点的四个场景，按显示顺序排列；siteId 1～4 是家和楼层
HARVEST_SITES: dict[int, Site] = {
    5: Site("初始的原野", "grassland.png", 33.333, 0, -40, -1, -1, True),
    7: Site("多彩的花田", "flowergarden.png", 24.806, -62.015, 20.672, -1, -1, True),
    6: Site("祝愿的沙滩", "beach.png", 20.513, 0, 80, 1, -1, False),
    8: Site("被遗忘的场所", "memorialplace.png", 21.333, 0, -106.667, 1, -1, False),
}

# 位置图上采集点圆点的颜色（拿不到本体贴图时用）
FIXTURE_COLORS: dict[int, str] = {
    112: "#f9f9f9",
    **dict.fromkeys(range(1001, 1005), "#da6d42"),
    2001: "#878685",
    2002: "#d5750a",
    2003: "#d5d5d5",
    2004: "#a7c7cb",
    2005: "#9933cc",
    3001: "#c95a49",
    **dict.fromkeys(range(4001, 4021), "#f8729a"),
    **dict.fromkeys([*range(5001, 5005), *range(5101, 5105)], "#f6f5f2"),
    6001: "#6f4e37",
    7001: "#a5d5ff",
}

RARE_ITEMS: dict[str, set[int]] = {
    "mysekai_material": {5, 12, 20, 24, 32, 33, 61, 62, 63, 64, 65},
    "mysekai_item": {7},
    "mysekai_fixture": {118, 119, 120, 121},
}
SUPER_RARE_ITEMS: dict[str, set[int]] = {
    "mysekai_material": {5, 12, 20, 24},
}


def rarity_level(category: str, item_id: int) -> int:
    """2: 超稀有，1: 稀有，0: 普通"""
    if item_id in SUPER_RARE_ITEMS.get(category, ()):
        return 2
    if item_id in RARE_ITEMS.get(category, ()):
        return 1
    return 0


# ============== masterdata ==============

# 文件名 → (mtime, 行, id 索引)；文件更新后按 mtime 重新加载
_tables: dict[str, tuple[float, list[dict], dict[int, dict]]] = {}


def _table(filename: str) -> tuple[list[dict], dict[int, dict]]:
    path = sekai_master_dir() / "master" / filename
    try:
        mtime = path.stat().st_mtime
        cached = _tables.get(filename)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # masterdata 由另一个仓库更新，可能缺文件或正写到一半
        logger.warning(f"masterdata 读取失败 {path}: {get_exc_desc(exc)}")
        return [], {}
    index = {row["id"]: row for row in rows}
    _tables[filename] = (mtime, rows, index)
    logger.debug(f"已加载 masterdata {filename}，记录数: {len(rows)}")
    return rows, index


def _row(filename: str, item_id: int) -> dict | None:
    return _table(filename)[1].get(item_id)


_ICON_TABLES = {
    "mysekai_material": "mysekaiMaterials.json",
    "mysekai_item": "mysekaiItems.json",
    "mysekai_fixture": "mysekaiFixtures.json",
}


def icon_asset_name(category: str, item_id: int) -> str | None:
    row = _row(_ICON_TABLES[category], item_id) or {}
    for key in ("iconAssetbundleName", "assetbundleName", "thumbnailAssetbundleName"):
        if row.get(key):
            return str(row[key])
    return None


def harvest_fixture_meta(fixture_id: int) -> tuple[str, str] | None:
    """收获点的 (assetbundleName, 稀有度)"""
    row = _row("mysekaiSiteHarvestFixtures.json", fixture_id) or {}
    asset = row.get("assetbundleName")
    rarity = row.get("mysekaiSiteHarvestFixtureRarityType")
    if not asset or rarity is None:
        return None
    return str(asset), str(rarity)


def _record_music(record_id: int) -> dict | None:
    record = _row("mysekaiMusicRecords.json", record_id)
    if record is None or record.get("mysekaiMusicTrackType") != "music":
        return None
    return _row("musics.json", int(record["externalId"]))


def record_name(record_id: int) -> str:
    """唱片显示名：masterdata 查得到歌名就用歌名"""
    title = (_record_music(record_id) or {}).get("title")
    return f"唱片：{title}" if title else f"唱片 (ID: {record_id})"


def record_jacket_bundle(record_id: int) -> str | None:
    return (_record_music(record_id) or {}).get("assetbundleName")


def _single_character_groups() -> dict[str, str]:
    """来访角色组 id → 角色名，只含单人组"""
    unit_to_char = {
        row["id"]: row["gameCharacterId"]
        for row in _table("gameCharacterUnits.json")[0]
    }
    result: dict[str, str] = {}
    for group in _table("mysekaiGameCharacterUnitGroups.json")[0]:
        unit_ids = [
            group[key]
            for key in (f"gameCharacterUnitId{i}" for i in range(1, 6))
            if key in group
        ]
        if len(unit_ids) != 1:
            continue
        name = CHARACTER_NAMES.get(unit_to_char.get(unit_ids[0]))
        if name:
            result[str(group["id"])] = name
    return result


# ============== 数据包 ==============


def decrypt(path: Path) -> dict[str, Any]:
    """国服 API 包体：AES-CBC + PKCS7 填充，里面是 msgpack"""
    key = settings.api_crypto.key.encode("utf-8")
    iv = settings.api_crypto.iv.encode("utf-8")
    try:
        plain = AES.new(key, AES.MODE_CBC, iv).decrypt(path.read_bytes())
        pad = plain[-1]
        if pad < 1 or pad > AES.block_size or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError("invalid PKCS7 padding")
        data = msgpack.unpackb(plain[:-pad])
        if not isinstance(data, dict):
            raise ValueError(f"顶层不是 dict: {type(data).__name__}")
    except Exception as exc:
        # 数据包是用户上传的，什么都可能有
        logger.warning(f"数据包解密失败 {path.name}: {get_exc_desc(exc)}")
        raise MsmError("文件解密失败，请检查文件格式是否正确。") from exc
    logger.debug(f"数据包解密成功: {path.name}")
    return data


def _gate_visits(decrypted: dict) -> list[dict]:
    return decrypted.get("userMysekaiGateCharacterVisit", {}).get(
        "userMysekaiGateCharacters", []
    )


def visit_group_ids(decrypted: dict) -> list[str]:
    """这份数据里所有来访角色组 id，记录来访历史用"""
    return [
        str(visit["mysekaiGameCharacterUnitGroupId"])
        for visit in _gate_visits(decrypted)
        if visit.get("mysekaiGameCharacterUnitGroupId")
    ]


@dataclass
class Point:
    location: tuple[int, int]
    fixture_id: int
    # {category: {item_id: 数量}}
    reward: dict[str, dict[int, int]] = field(default_factory=dict)


@dataclass
class Visitor:
    group_id: str
    name: str
    count: int


@dataclass
class MsrData:
    # siteId → 已刷新的采集点；包括家和楼层
    maps: dict[int, list[Point]]
    # siteId → {(category, item_id): 数量}；没有掉落的场景不在里面
    materials: dict[int, dict[tuple[str, int], int]]
    visitors: list[Visitor]
    owned_records: set[int]


def _parse_maps(decrypted: dict) -> dict[int, list[Point]]:
    # 服务端把采集图压成位置数组，不再是带字段名的对象：
    # 地图：[siteId, fixtures, drops]
    # 采集点：[fixtureId, x, z, hp, status, extra]
    # 掉落：[resourceType, resourceId, x, z, hp, seq, status, quantity, extra]
    maps: dict[int, list[Point]] = {}
    for site_id, fixtures, drops in decrypted["updatedResources"][
        "userMysekaiHarvestMaps"
    ]:
        points: dict[tuple[int, int], Point] = {}
        for fixture in fixtures:
            location = (fixture[1], fixture[2])
            if fixture[4] == "spawned" and location not in points:
                points[location] = Point(location, fixture[0])
        for drop in drops:
            point = points.get((drop[2], drop[3]))
            if point is None:
                continue
            items = point.reward.setdefault(drop[0], {})
            items[drop[1]] = items.get(drop[1], 0) + drop[7]
        maps[site_id] = list(points.values())
    return maps


def analyze(decrypted: dict) -> MsrData:
    try:
        maps = _parse_maps(decrypted)
    except (KeyError, IndexError, TypeError, ValueError):
        logger.exception("地图数据解析失败")
        raise MsmError("地图数据解析失败。")

    materials: dict[int, dict[tuple[str, int], int]] = {}
    for site_id, points in maps.items():
        summary: dict[tuple[str, int], int] = {}
        for point in points:
            for category, items in point.reward.items():
                for item_id, quantity in items.items():
                    key = (category, item_id)
                    summary[key] = summary.get(key, 0) + quantity
        if summary:
            materials[site_id] = summary

    group_names = _single_character_groups()
    visitors = [
        Visitor(group_id, group_names[group_id], visit["visitCount"])
        for visit in _gate_visits(decrypted)
        if visit.get("visitCount")
        and (group_id := str(visit.get("mysekaiGameCharacterUnitGroupId")))
        in group_names
    ]

    owned_records = {
        record["mysekaiMusicRecordId"]
        for record in decrypted.get("updatedResources", {}).get(
            "userMysekaiMusicRecords", []
        )
        if record.get("mysekaiMusicRecordId")
    }
    return MsrData(maps, materials, visitors, owned_records)
