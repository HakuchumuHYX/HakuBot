# plugins/buaa_msm/analysis.py
"""
材料分析模块（analysis）

职责：
- 数据聚合（aggregate_materials）
- 资源名称查询（get_resource_name / get_translated_map_name）
- 来访角色统计（get_visiting_group_counts）
- 唱片信息（parse_owned_music_records / get_jacket_url）

注意：
- 本模块不直接进行文件 I/O，所有 masterdata 查询走 masterdata_lite
- 中文名称映射内联在 domain/constants.py 中
"""

from __future__ import annotations

from typing import Any

from nonebot.log import logger

from .domain.constants import (
    CHARACTER_NAMES,
    MAP_ORDER,
    MAP_TITLE_CN,
    RESOURCE_NAME_CN,
)
from .config import plugin_config
from .services.masterdata_lite import masterdata_lite

# ============== 类型别名 ==============

AggregatedData = dict[str, dict[tuple[str, str], int]]


# ============== 辅助函数 ==============


def get_translated_map_name(japanese_name: str) -> str:
    """获取翻译后的地图名称"""
    return MAP_TITLE_CN.get(japanese_name, japanese_name)


def get_resource_name(category: str, item_id: str) -> str:
    """
    获取资源中文名称。查询链：
    1. 唱片特殊处理（通过 masterdata 查音乐标题）
    2. 内联中文名映射（RESOURCE_NAME_CN）
    3. masterdata_lite 动态名称
    4. category 默认名 + ID
    5. "{category}_{item_id}" 兜底
    """
    if category == "mysekai_music_record":
        title = masterdata_lite.get_music_title_by_record_id(str(item_id))
        if title:
            return f"唱片：{title}"
        default_name = RESOURCE_NAME_CN.get(category, {}).get("_default", "唱片")
        return f"{default_name} (ID: {item_id})"

    # 内联中文名
    category_names = RESOURCE_NAME_CN.get(category, {})
    name = category_names.get(str(item_id))
    if name:
        return name

    # masterdata 动态名称
    try:
        md_name = masterdata_lite.get_resource_name(category, int(item_id))
        if md_name:
            return md_name
    except Exception:
        pass

    # category 默认名
    default_name = category_names.get("_default")
    if default_name:
        return f"{default_name} ({item_id})"

    return f"{category}_{item_id}"


# ============== 数据聚合 ==============


def aggregate_materials(parsed_maps: dict[str, list]) -> AggregatedData:
    """聚合材料数据"""
    analysis_results: AggregatedData = {}

    for map_name, locations in parsed_maps.items():
        if not locations:
            continue

        map_summary: dict[tuple[str, str], int] = {}
        for point in locations:
            for category, items in point.get("reward", {}).items():
                for item_id, quantity in items.items():
                    key = (category, str(item_id))
                    map_summary[key] = map_summary.get(key, 0) + quantity

        if map_summary:
            analysis_results[map_name] = map_summary

    return analysis_results


# ============== 来访角色统计 ==============


def get_visiting_group_counts(decrypted_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """获取来访角色统计（仅单人组合）"""
    final_group_counts: dict[str, dict[str, Any]] = {}

    try:
        char_visit_list = (
            decrypted_data.get("userMysekaiGateCharacterVisit", {})
            .get("userMysekaiGateCharacters", [])
        )

        group_id_to_name = masterdata_lite.build_group_id_to_char_name(CHARACTER_NAMES)

        for visit_group in char_visit_list:
            group_id = str(visit_group.get("mysekaiGameCharacterUnitGroupId"))
            visit_count = visit_group.get("visitCount", 0)

            if not group_id or visit_count == 0:
                continue

            name = group_id_to_name.get(group_id)
            if not name:
                continue

            final_group_counts[group_id] = {"name": name, "count": visit_count}

        return final_group_counts
    except Exception as e:
        logger.error(f"解析来访角色组合失败: {e}")
        return {}


def parse_owned_music_records(decrypted_data: dict[str, Any]) -> set[str]:
    """解析已拥有的唱片"""
    owned_ids: set[str] = set()
    try:
        record_list = (
            decrypted_data.get("updatedResources", {})
            .get("userMysekaiMusicRecords", [])
        )
        for record in record_list:
            record_id = record.get("mysekaiMusicRecordId")
            if record_id:
                owned_ids.add(str(record_id))
        logger.info(f"解析到 {len(owned_ids)} 条已获得的唱片记录。")
    except Exception as e:
        logger.error(f"解析已获得唱片列表失败: {e}")
    return owned_ids


# ============== 封面 URL 辅助 ==============


def get_jacket_url(mysekai_record_id: str) -> str | None:
    """
    根据 mysekai_record_id 获取封面的 asset 站 URL。
    返回 None 表示无法映射或未配置 asset 站。
    """
    asset_base = plugin_config.asset_url_base.strip("/")
    if not asset_base:
        return None
    server = plugin_config.asset_server

    bundle = masterdata_lite.get_jacket_bundle_by_record_id(str(mysekai_record_id))
    if not bundle:
        return None
    return f"{asset_base}/{server}-assets/startapp/music/jacket/{bundle}/{bundle}.png"


def get_all_needed_jacket_urls(record_ids: list[str]) -> dict[str, str]:
    """
    批量获取需要下载的封面 URL。
    返回 {mysekai_record_id: url} 字典，仅包含能成功映射的。
    """
    result: dict[str, str] = {}
    for rid in record_ids:
        url = get_jacket_url(rid)
        if url:
            result[rid] = url
    return result
