# plugins/buaa_msm/parsers/map_parser.py
"""
地图数据解析（从 decrypted_data -> parsed_maps）。

说明：
- 从 `paint.py` 抽离出来的“解析层”逻辑，渲染层不应承担解析职责。
- 返回结构保持与原 `paint.parse_map` 一致，确保功能不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nonebot.log import logger

from plugins.buaa_msm.domain.constants import SITE_ID_MAP


# 服务端现在把采集图压成位置数组，不再是带字段名的对象：
# 地图：[siteId, fixtures, drops]
# 采集点：[fixtureId, x, z, hp, status, extra]
# 掉落：[resourceType, resourceId, x, z, hp, seq, status, quantity, extra]


def parse_map(user_data: Dict[str, Any]) -> Optional[Dict[str, List]]:
    """从解密的字典数据中解析地图采集点信息。"""
    if "updatedResources" not in user_data:
        logger.error("Error: 'updatedResources' not found in decrypted data.")
        return None

    if "userMysekaiHarvestMaps" not in user_data.get("updatedResources", {}):
        logger.error("Error: 'userMysekaiHarvestMaps' not found in decrypted data.")
        return None

    try:
        processed_map: Dict[str, List] = {}
        for site_id, fixtures, drops in user_data["updatedResources"][
            "userMysekaiHarvestMaps"
        ]:
            site_name = SITE_ID_MAP.get(site_id, f"Unknown Site {site_id}")
            mp_detail: List[Dict[str, Any]] = []

            for fixture in fixtures:
                if fixture[4] != "spawned":
                    continue
                mp_detail.append(
                    {
                        "location": (fixture[1], fixture[2]),
                        "fixtureId": fixture[0],
                        "reward": {},
                    }
                )

            for drop in drops:
                pos = (drop[2], drop[3])
                resource_type = drop[0]
                resource_id = drop[1]
                quantity = drop[7]
                for detail in mp_detail:
                    if detail["location"] != pos:
                        continue
                    detail["reward"].setdefault(resource_type, {})
                    detail["reward"][resource_type][resource_id] = (
                        detail["reward"][resource_type].get(resource_id, 0) + quantity
                    )
                    break

            processed_map[str(site_name)] = mp_detail
    except Exception as e:
        logger.error(f"Error decoding map data: {e}")
        return None

    return processed_map
