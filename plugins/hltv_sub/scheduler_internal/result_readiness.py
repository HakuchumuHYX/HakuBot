"""Result push readiness checks for HLTV scheduler."""

from __future__ import annotations

from typing import Optional

from ..models import MapStats, MatchStats, ResultInfo


def _is_final_round_score(score1: int, score2: int) -> bool:
    winner = max(score1, score2)
    loser = min(score1, score2)

    if winner == 13:
        return loser <= 11

    if winner >= 16 and (winner - 16) % 3 == 0:
        return winner - loser >= 2

    return False


def _expected_maps_from_result(result: ResultInfo) -> tuple[int, str, Optional[str]]:
    if not (str(result.score1).isdigit() and str(result.score2).isdigit()):
        return 0, "", None

    score1 = int(result.score1)
    score2 = int(result.score2)

    if max(score1, score2) > 5:
        if not _is_final_round_score(score1, score2):
            return 0, "", f"BO1 回合比分未达到终局: score={score1}-{score2}"
        return 1, "round_score_like_result", None

    return score1 + score2, "map_score_result", None


def _played_maps(stats: MatchStats) -> list[MapStats]:
    return [
        m
        for m in (stats.maps or [])
        if m.score_team1 != "-" and m.score_team2 != "-"
    ]


def _score_pair(score1: str, score2: str) -> Optional[tuple[int, int]]:
    if not (str(score1).isdigit() and str(score2).isdigit()):
        return None
    return int(score1), int(score2)


def is_final_round_score(score1: int, score2: int) -> bool:
    return _is_final_round_score(score1, score2)


def score_pair(score1: str, score2: str) -> Optional[tuple[int, int]]:
    return _score_pair(score1, score2)


def has_complete_map_details(stats: MatchStats, map_info: MapStats) -> bool:
    players = (stats.map_stats_details or {}).get(map_info.map_name) or []
    teams = {getattr(player, "team", "") for player in players}
    return bool(players) and {"team1", "team2"}.issubset(teams)


def _score_pairs_match(
    score1: str,
    score2: str,
    expected_score1: str,
    expected_score2: str,
) -> bool:
    score_pair = _score_pair(score1, score2)
    expected_pair = _score_pair(expected_score1, expected_score2)
    return score_pair is not None and expected_pair is not None and score_pair == expected_pair


def _unfinished_played_maps(played_maps: list[MapStats]) -> list[str]:
    unfinished: list[str] = []
    for map_info in played_maps:
        score_pair = _score_pair(map_info.score_team1, map_info.score_team2)
        if score_pair is None or not _is_final_round_score(*score_pair):
            unfinished.append(
                f"{map_info.map_name}({map_info.score_team1}-{map_info.score_team2})"
            )
    return unfinished


def _missing_or_incomplete_played_map_details(
    stats: MatchStats,
    played_maps: list[MapStats],
) -> list[str]:
    return [
        map_info.map_name
        for map_info in played_maps
        if not has_complete_map_details(stats, map_info)
    ]


def get_result_stats_push_block_reason(
    result: ResultInfo,
    stats: Optional[MatchStats],
) -> Optional[str]:
    """Return a reason when result stats should wait for a later poll."""
    if stats is None:
        return "stats 未获取到"

    expected_maps, expected_maps_reason, score_block_reason = _expected_maps_from_result(result)
    if score_block_reason:
        return score_block_reason

    if expected_maps <= 0:
        return None

    if not _score_pairs_match(stats.score1, stats.score2, result.score1, result.score2):
        return (
            "stats 总比分未同步到结果页: "
            f"result={result.score1}-{result.score2}, stats={stats.score1}-{stats.score2}"
        )

    played_maps = _played_maps(stats)
    if len(played_maps) != expected_maps:
        return (
            "stats 未更新完整: "
            f"expected_maps={expected_maps}, played_maps={len(played_maps)}, "
            f"reason={expected_maps_reason}"
        )

    unfinished_maps = _unfinished_played_maps(played_maps)
    if unfinished_maps:
        return f"单图比分未达到终局: unfinished_maps={unfinished_maps}"

    if expected_maps == 1:
        only_map = played_maps[0]
        if not _score_pairs_match(
            only_map.score_team1,
            only_map.score_team2,
            result.score1,
            result.score2,
        ):
            return (
                "BO1 单图比分未同步到结果页: "
                f"result={result.score1}-{result.score2}, "
                f"map={only_map.score_team1}-{only_map.score_team2}"
            )

    incomplete_details = _missing_or_incomplete_played_map_details(stats, played_maps)
    if incomplete_details:
        return f"单图数据未更新完整: incomplete_map_details={incomplete_details}"

    return None
