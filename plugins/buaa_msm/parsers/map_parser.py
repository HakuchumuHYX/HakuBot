# plugins/buaa_msm/parsers/map_parser.py
"""
地图数据解析（从 decrypted_data -> parsed_maps）。

说明：
- 从 `paint.py` 抽离出来的“解析层”逻辑，渲染层不应承担解析职责。
- 返回结构保持与原 `paint.parse_map` 一致，确保功能不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import msgspec
from nonebot.log import logger

from ..domain.constants import SITE_ID_MAP


# ============== msgspec 数据结构（与原 paint.py 保持一致） ==============


class UserMysekaiSiteHarvestFixture(msgspec.Struct):
    mysekaiSiteHarvestFixtureId: int
    positionX: int
    positionZ: int
    hp: int
    userMysekaiSiteHarvestFixtureStatus: str


class UserMysekaiSiteHarvestResourceDrop(msgspec.Struct):
    resourceType: str
    resourceId: int
    positionX: int
    positionZ: int
    hp: int
    seq: int
    mysekaiSiteHarvestResourceDropStatus: str
    quantity: int


class Map(msgspec.Struct, kw_only=True):
    mysekaiSiteId: int
    siteName: Optional[str] = None
    userMysekaiSiteHarvestFixtures: List[UserMysekaiSiteHarvestFixture]
    userMysekaiSiteHarvestResourceDrops: List[UserMysekaiSiteHarvestResourceDrop]


def parse_map(user_data: Dict[str, Any]) -> Optional[Dict[str, List]]:
    """从解密的字典数据中解析地图采集点信息（结构与原 paint.parse_map 一致）"""
    if "updatedResources" not in user_data:
        logger.error("Error: 'updatedResources' not found in decrypted data.")
        return None

    if "userMysekaiHarvestMaps" not in user_data.get("updatedResources", {}):
        logger.error("Error: 'userMysekaiHarvestMaps' not found in decrypted data.")
        return None

    try:
        harvest_maps: List[Map] = [
            msgspec.json.decode(msgspec.json.encode(mp), type=Map)
            for mp in user_data["updatedResources"]["userMysekaiHarvestMaps"]
        ]
    except Exception as e:
        logger.error(f"Error decoding map data with msgspec: {e}")
        return None

    for mp in harvest_maps:
        mp.siteName = SITE_ID_MAP.get(mp.mysekaiSiteId, f"Unknown Site {mp.mysekaiSiteId}")

    processed_map: Dict[str, List] = {}
    for mp in harvest_maps:
        mp_detail: List[Dict[str, Any]] = []

        for fixture in mp.userMysekaiSiteHarvestFixtures:
            if fixture.userMysekaiSiteHarvestFixtureStatus == "spawned":
                mp_detail.append(
                    {
                        "location": (fixture.positionX, fixture.positionZ),
                        "fixtureId": fixture.mysekaiSiteHarvestFixtureId,
                        "reward": {},
                    }
                )

        for drop in mp.userMysekaiSiteHarvestResourceDrops:
            pos = (drop.positionX, drop.positionZ)
            for i in range(len(mp_detail)):
                if mp_detail[i]["location"] != pos:
                    continue
                mp_detail[i]["reward"].setdefault(drop.resourceType, {})
                mp_detail[i]["reward"][drop.resourceType][drop.resourceId] = (
                    mp_detail[i]["reward"][drop.resourceType].get(drop.resourceId, 0) + drop.quantity
                )
                break

        processed_map[str(mp.siteName)] = mp_detail

    return processed_map
