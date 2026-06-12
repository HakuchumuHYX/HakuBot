"""Build interim map-result notification candidates for live BO3/BO5 matches."""

from __future__ import annotations

from ..models import MapStats, MatchStats
from .result_readiness import (
    has_complete_map_details,
    is_final_round_score,
    score_pair,
)
from .types import CompletedMapResult


def _maps_needed_to_win(bo_maps: int) -> int:
    return bo_maps // 2 + 1


def _map_notification_id(match_id: str, map_index: int, map_info: MapStats) -> str:
    stable_map_id = map_info.stats_id or f"{map_index}:{map_info.map_name}"
    return f"{match_id}:{stable_map_id}"


def _single_map_stats(
    *,
    stats: MatchStats,
    map_info: MapStats,
    score1_after_map: int,
    score2_after_map: int,
) -> MatchStats:
    map_players = (stats.map_stats_details or {}).get(map_info.map_name) or []
    return MatchStats(
        match_id=stats.match_id,
        team1=stats.team1,
        team2=stats.team2,
        score1=str(score1_after_map),
        score2=str(score2_after_map),
        status=stats.status,
        maps=[map_info],
        players=[],
        map_stats_details={map_info.map_name: map_players},
        vetos=stats.vetos,
        event=stats.event,
    )


def build_completed_map_results(
    *,
    event_id: str,
    event_title: str,
    match_id: str,
    team1: str,
    team2: str,
    bo_maps: int,
    stats: MatchStats | None,
) -> list[CompletedMapResult]:
    if stats is None or bo_maps not in {3, 5}:
        return []

    maps_needed = _maps_needed_to_win(bo_maps)
    score1_after_map = 0
    score2_after_map = 0
    candidates: list[CompletedMapResult] = []

    for map_index, map_info in enumerate(stats.maps or [], start=1):
        pair = score_pair(map_info.score_team1, map_info.score_team2)
        if pair is None:
            continue

        round_score1, round_score2 = pair
        if not is_final_round_score(round_score1, round_score2):
            continue

        if round_score1 > round_score2:
            score1_after_map += 1
        else:
            score2_after_map += 1

        if not has_complete_map_details(stats, map_info):
            continue

        if max(score1_after_map, score2_after_map) >= maps_needed:
            continue

        candidates.append(
            CompletedMapResult(
                event_id=event_id,
                event_title=event_title,
                match_id=match_id,
                team1=team1,
                team2=team2,
                bo_maps=bo_maps,
                map_index=map_index,
                map_name=map_info.map_name,
                notification_id=_map_notification_id(match_id, map_index, map_info),
                score1_after_map=str(score1_after_map),
                score2_after_map=str(score2_after_map),
                single_map_stats=_single_map_stats(
                    stats=stats,
                    map_info=map_info,
                    score1_after_map=score1_after_map,
                    score2_after_map=score2_after_map,
                ),
            )
        )

    return candidates
