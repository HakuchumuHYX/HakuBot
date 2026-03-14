# plugins/buaa_msm/analysis.py
"""
材料分析模块（analysis）

职责：
- 负责“数据聚合/翻译/名称查询/稀有度颜色”等业务规则
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from nonebot.log import logger

from .config import CHARACTER_NAMES, MAP_ORDER, plugin_config
from .resources.catalog import ITEM_TEXTURES, RARE_ITEM, SUPER_RARE_ITEM
from .services.masterdata_lite import masterdata_lite

# 从配置获取路径
resource_dir = plugin_config.resource_dir

# ============== 翻译和数据映射 ==============

RESOURCE_NAMES: Dict[Tuple[str, str], str] = {}
TRANSLATIONS: Dict[str, Any] = {"map_titles": {}, "resource_names": {}}
MUSIC_TITLES: Dict[str, str] = {}
MUSIC_JACKET_MAP: Dict[str, str] = {}  # music_id -> assetBundleName
MYSEKAI_MUSIC_MAP: Dict[str, str] = {}
CHARACTER_GROUP_MAP: Dict[str, List[str]] = {}
CHARACTER_UNIT_MAP: Dict[str, str] = {}
CHARACTER_NAME_MAP: Dict[str, str] = {}
GROUP_ID_TO_CHAR_NAME: Dict[str, str] = {}

# 翻译文件路径
translations_file = resource_dir / "translations.json"

# 外部数据文件路径
musics_file = plugin_config.master_data_dir / "musics.json"
MYSEKAI_RECORDS_FILE = plugin_config.master_data_dir / "mysekaiMusicRecords.json"
GROUPS_FILE = plugin_config.master_data_dir / "mysekaiGameCharacterUnitGroups.json"
UNITS_FILE = plugin_config.master_data_dir / "gameCharacterUnits.json"
PROFILES_FILE = plugin_config.master_data_dir / "characterProfiles.json"


def _build_resource_names():
    """构建资源名称映射（无翻译时的 fallback）"""
    global RESOURCE_NAMES
    for category, items in ITEM_TEXTURES.items():
        for item_id, path_str in items.items():
            name = Path(path_str).stem
            name = name.replace("item_", "").replace("mdl_", "").replace("before_", "")
            RESOURCE_NAMES[(category, str(item_id))] = name
    logger.info(f"Analysis 模块已加载 {len(RESOURCE_NAMES)} 个回退资源名称。")


def load_translations():
    """加载翻译文件"""
    global TRANSLATIONS
    if not translations_file.exists():
        return
    try:
        with open(translations_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            TRANSLATIONS["map_titles"] = data.get("map_titles", {})
            TRANSLATIONS["resource_names"] = data.get("resource_names", {})
    except Exception as e:
        logger.error(f"加载 translations.json 失败: {e}")


def load_music_titles():
    """加载音乐标题和封面 asset bundle 名"""
    global MUSIC_TITLES, MUSIC_JACKET_MAP
    try:
        if not musics_file.exists():
            return
        with open(musics_file, "r", encoding="utf-8") as f:
            musics_data = json.load(f)
        for music in musics_data:
            mid = str(music.get("id", ""))
            if mid and "title" in music:
                MUSIC_TITLES[mid] = music["title"]
            bundle = music.get("assetbundleName")
            if mid and bundle:
                MUSIC_JACKET_MAP[mid] = bundle
    except Exception as e:
        logger.error(f"加载 musics.json 失败: {e}")


def load_mysekai_music_map():
    """加载 MySekai 音乐映射（mysekaiMusicRecords -> musics.externalId）"""
    global MYSEKAI_MUSIC_MAP
    try:
        if not MYSEKAI_RECORDS_FILE.exists():
            return
        with open(MYSEKAI_RECORDS_FILE, "r", encoding="utf-8") as f:
            records_data = json.load(f)
        for record in records_data:
            if record.get("mysekaiMusicTrackType") == "music":
                mysekai_id = str(record.get("id"))
                external_id = str(record.get("externalId"))
                MYSEKAI_MUSIC_MAP[mysekai_id] = external_id
    except Exception as e:
        logger.error(f"加载 mysekaiMusicRecords.json 失败: {e}")


def load_character_data():
    """加载角色数据（用于来访角色统计展示）"""
    global CHARACTER_GROUP_MAP, CHARACTER_UNIT_MAP, CHARACTER_NAME_MAP, GROUP_ID_TO_CHAR_NAME

    try:
        CHARACTER_NAME_MAP = {str(k): v for k, v in CHARACTER_NAMES.items()}

        # 加载角色单位
        if UNITS_FILE.exists():
            with open(UNITS_FILE, "r", encoding="utf-8") as f:
                units_data = json.load(f)
            for unit in units_data:
                unit_id = str(unit.get("id"))
                char_id = str(unit.get("gameCharacterId"))
                CHARACTER_UNIT_MAP[unit_id] = char_id

        # 加载角色组合
        if GROUPS_FILE.exists():
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                groups_data = json.load(f)
            for group in groups_data:
                group_id = str(group.get("id"))
                unit_ids = []
                for i in range(1, 6):
                    key = f"gameCharacterUnitId{i}"
                    if key in group:
                        unit_ids.append(str(group[key]))
                CHARACTER_GROUP_MAP[group_id] = unit_ids

                # 只做单人组合映射
                if len(unit_ids) == 1:
                    unit_id = unit_ids[0]
                    char_id = CHARACTER_UNIT_MAP.get(unit_id)
                    if char_id:
                        name = CHARACTER_NAME_MAP.get(char_id)
                        if name:
                            GROUP_ID_TO_CHAR_NAME[group_id] = name
    except Exception as e:
        logger.error(f"加载角色数据失败: {e}")


# ============== 辅助函数 ==============


def get_translated_map_name(japanese_name: str) -> str:
    """获取翻译后的地图名称"""
    return TRANSLATIONS["map_titles"].get(japanese_name, japanese_name)


def get_resource_name(category: str, item_id: str) -> str:
    """获取资源名称"""
    if category == "mysekai_music_record":
        mysekai_id = item_id
        external_id = MYSEKAI_MUSIC_MAP.get(mysekai_id)
        if external_id:
            title = MUSIC_TITLES.get(external_id)
            if title:
                return f"唱片：{title}"
        default_name = TRANSLATIONS["resource_names"].get(category, {}).get("default", "唱片")
        return f"{default_name} (ID: {mysekai_id})"

    category_names = TRANSLATIONS["resource_names"].get(category, {})
    name = category_names.get(str(item_id))
    if name:
        return name

    # 动态 masterdata 名称（翻译缺失时优先）
    try:
        md_name = masterdata_lite.get_resource_name(category, int(item_id))
        if md_name:
            return md_name
    except Exception:
        pass

    default_name = category_names.get("default")
    if default_name:
        return f"{default_name} ({item_id})"

    fallback_name = RESOURCE_NAMES.get((category, str(item_id)))
    if fallback_name:
        return fallback_name

    return f"{category}_{item_id}"


def get_rarity_color(category: str, item_id: str) -> Tuple[int, int, int]:
    """获取稀有度颜色"""
    try:
        item_id_int = int(item_id)
    except ValueError:
        return (0, 0, 0)

    if category in SUPER_RARE_ITEM and item_id_int in SUPER_RARE_ITEM[category]:
        return (255, 0, 0)
    if category in RARE_ITEM and item_id_int in RARE_ITEM[category]:
        return (0, 0, 255)
    return (0, 0, 0)


# ============== 数据聚合 ==============

AggregatedData = Dict[str, Dict[Tuple[str, str], int]]


def aggregate_materials(parsed_maps: Dict[str, List]) -> AggregatedData:
    """聚合材料数据"""
    analysis_results: AggregatedData = {}

    for map_name, locations in parsed_maps.items():
        if not locations:
            continue

        map_summary: Dict[Tuple[str, str], int] = {}
        for point in locations:
            for category, items in point.get("reward", {}).items():
                for item_id, quantity in items.items():
                    key = (category, str(item_id))
                    map_summary[key] = map_summary.get(key, 0) + quantity

        if map_summary:
            analysis_results[map_name] = map_summary

    return analysis_results


def get_visiting_group_counts(decrypted_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """获取来访角色统计（仅单人组合）"""
    final_group_counts: Dict[str, Dict[str, Any]] = {}

    try:
        char_visit_list = decrypted_data.get("userMysekaiGateCharacterVisit", {}).get("userMysekaiGateCharacters", [])

        for visit_group in char_visit_list:
            group_id = str(visit_group.get("mysekaiGameCharacterUnitGroupId"))
            visit_count = visit_group.get("visitCount", 0)

            if not group_id or visit_count == 0:
                continue

            unit_ids = CHARACTER_GROUP_MAP.get(group_id)
            if not unit_ids:
                continue

            if len(unit_ids) > 1:
                continue

            name = GROUP_ID_TO_CHAR_NAME.get(group_id)
            if not name:
                continue

            final_group_counts[group_id] = {"name": name, "count": visit_count}

        return final_group_counts
    except Exception as e:
        logger.error(f"解析来访角色组合失败: {e}")
        return {}


def parse_owned_music_records(decrypted_data: Dict[str, Any]) -> Set[str]:
    """解析已拥有的唱片"""
    owned_ids: Set[str] = set()
    try:
        record_list = decrypted_data.get("updatedResources", {}).get("userMysekaiMusicRecords", [])
        for record in record_list:
            record_id = record.get("mysekaiMusicRecordId")
            if record_id:
                owned_ids.add(str(record_id))
        logger.info(f"解析到 {len(owned_ids)} 条已获得的唱片记录。")
    except Exception as e:
        logger.error(f"解析已获得唱片列表失败: {e}")
    return owned_ids


# ============== 封面 URL 辅助 ==============


def get_jacket_url(mysekai_record_id: str) -> Optional[str]:
    """
    根据 mysekai_record_id 获取封面的 asset 站 URL。
    返回 None 表示无法映射或未配置 asset 站。
    """
    asset_base = plugin_config.asset_url_base.strip("/")
    if not asset_base:
        return None
    server = plugin_config.asset_server
    external_id = MYSEKAI_MUSIC_MAP.get(str(mysekai_record_id))
    if not external_id:
        return None
    bundle = MUSIC_JACKET_MAP.get(external_id)
    if not bundle:
        return None
    return f"{asset_base}/{server}-assets/startapp/music/jacket/{bundle}/{bundle}.png"


def get_all_needed_jacket_urls(record_ids: List[str]) -> Dict[str, str]:
    """
    批量获取需要下载的封面 URL。
    返回 {mysekai_record_id: url} 字典，仅包含能成功映射的。
    """
    result: Dict[str, str] = {}
    for rid in record_ids:
        url = get_jacket_url(rid)
        if url:
            result[rid] = url
    return result


# ============== 初始化 ==============

_build_resource_names()
load_translations()
load_music_titles()
load_mysekai_music_map()
load_character_data()
