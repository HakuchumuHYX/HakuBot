"""接口数据 → 模板上下文 → 图片。

三种模式（完美天梯 pvp、官匹 mm、5E）各有一个生涯 view 和单局 view 构造函数。
共用字段同名同义，锐评只读共用字段；各平台模板专用的字段（如 5E 的 honors、best）只出现在各自的 view 里。
锐评的输入从 view 里取；最近对局只有前 RECENT_ROWS 场（recent）画在卡片上，锐评读完整的 history。
"""

import math
from datetime import datetime

from nonebot import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from plugins.cs_stats.config import TEMPLATE_DIR, plugin_config
from plugins.cs_stats.fivee_api import avatar_url, level_icon, level_name
from utils.rendering.engine import render_template

WIDTH = 720
# 头像、地图、武器图都是远程资源；等不到就渲染无图版，不退回文字
IMAGE_TIMEOUT_MS = 15_000
MODE_LABEL = {"pvp": "完美天梯", "mm": "官方匹配", "5e": "5E 优先排位"}
# 完美生涯卡的柱状图和锐评看最近这么多场；卡片的逐场列表只画前几场，再长图就太长了
HISTORY_MATCHES = 20
RECENT_ROWS = 5
# 完美天梯 2401 分起是 S 段，S 段内按星数分四档（0/10/25/50 星），接口的 slevel 就是档位
PW_S_SCORE = 2401
PW_S_TIERS = {0: "青铜", 1: "黄金", 2: "钻石", 3: "魔王"}
RADAR_LABELS = (("shot", "枪法"), ("victory", "致胜"), ("breach", "突破"), ("snipe", "狙击"), ("prop", "道具"))
# 完美单局的 first 列出本人在本队排第一的指标（second、third 是第二、第三，没用到）；不是全场排名
RANK_FIELDS = {
    "entryKill": "首杀", "entryDeath": "首死", "smokeThrows": "烟雾弹", "flashSuccess": "有效闪光",
    "flashTeammate": "闪到队友", "headShotRatio": "爆头率", "assist": "助攻", "kast": "KAST",
    "grenadeDamage": "道具伤害", "kill": "击杀", "death": "死亡", "rating": "Rating", "adpr": "ADR",
    "rws": "RWS", "awpKill": "狙杀", "throwsCnt": "投掷物", "tradeFragCount": "补枪",
    "bombPlanted": "下包", "bombDefused": "拆包", "handGunKill": "手枪击杀", "infernoDamage": "燃烧弹伤害",
    "snipeNum": "狙杀", "mvpValue": "MVP", "firstDeath": "首死",
}


def _f(value, digits: int = 2) -> str:
    return f"{float(value or 0):.{digits}f}"


def _group(label: str, parts: dict) -> dict:
    """「首杀/首死 73/76」这种合并显示的格子；parts 给锐评按单项读"""
    parts = {key: str(value) for key, value in parts.items()}
    return {"label": label, "value": "/".join(parts.values()), "parts": parts}


def _pct(ratio) -> str:
    return f"{float(ratio or 0) * 100:.0f}%"


def _ts(value) -> str:
    return datetime.fromtimestamp(int(value)).strftime("%m-%d %H:%M")


def _sparkline(values: list[float], width: int = 300, height: int = 56) -> dict | None:
    """values 按时间正序；返回折线点和首尾值，少于 2 个点不画"""
    values = [float(v) for v in values if v is not None]
    if len(values) < 2:
        return None
    low, high = min(values), max(values)
    span = (high - low) or 1
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i * step:.1f},{height - 4 - (v - low) / span * (height - 8):.1f}" for i, v in enumerate(values)
    )
    return {"points": points, "first": values[0], "last": values[-1], "low": low, "high": high, "count": len(values)}


def _bars(values: list) -> dict | None:
    """逐场 Rating 柱：1.0 以上算打出来了；高度按本组最大值和 1.5 中的较大者缩放"""
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    top = max(max(values), 1.5)
    return {
        "items": [{"value": f"{v:.2f}", "pct": max(v / top * 100, 3), "good": v >= 1} for v in values],
        "baseline": 100 / top,
        "count": len(values),
    }


def _radar(stats: dict, size: int = 150) -> dict:
    """完美的五维评分是 0～10"""
    center, radius = size / 2, size / 2 - 22
    axes, points = [], []
    for i, (key, label) in enumerate(RADAR_LABELS):
        angle = -math.pi / 2 + i * 2 * math.pi / len(RADAR_LABELS)
        value = max(0.0, min(10.0, float(stats.get(key) or 0)))
        cos, sin = math.cos(angle), math.sin(angle)
        points.append(f"{center + cos * radius * value / 10:.1f},{center + sin * radius * value / 10:.1f}")
        axes.append({
            "label": label,
            "value": f"{value:.1f}",
            "x2": f"{center + cos * radius:.1f}",
            "y2": f"{center + sin * radius:.1f}",
            "lx": f"{center + cos * (radius + 13):.1f}",
            "ly": f"{center + sin * (radius + 13):.1f}",
        })
    rings = [
        " ".join(
            f"{center + math.cos(-math.pi / 2 + i * 2 * math.pi / 5) * radius * r:.1f},"
            f"{center + math.sin(-math.pi / 2 + i * 2 * math.pi / 5) * radius * r:.1f}"
            for i in range(5)
        )
        for r in (0.25, 0.5, 0.75, 1)
    ]
    return {"size": size, "center": center, "axes": axes, "points": " ".join(points), "rings": rings}


def premier_tier(score: int) -> int:
    """CS2 Premier 分段颜色：每 5000 一档，30000 以上为最高档"""
    return min(max(score, 0) // 5000, 6)


def _score(label: str, value: int | None, *, name=None, stars=None, icon="", plate="", position=None) -> dict:
    """三个生涯 view 的 score 同形：段位名、星数、段位图标、分数牌颜色 class、排名，没有的留空"""
    return {
        "label": label, "value": value, "name": name, "stars": stars,
        "icon": icon, "plate": plate, "position": position,
    }


def _pw_s(score) -> bool:
    return int(score or 0) >= PW_S_SCORE


def _pvp_score(stats: dict) -> dict:
    """S 段的 pvpScore 锁在 2401；隐藏分、星数、S 段档位在 scoreList 最新一条"""
    score = int(stats.get("pvpScore") or 0)
    if not _pw_s(score):
        return _score("天梯分", score)
    latest = stats["scoreList"][0]
    slevel = int(latest.get("slevel") or 0)
    return _score(
        "天梯分",
        int(latest["score"]),
        name=f"{PW_S_TIERS.get(slevel, '')} S".strip(),
        stars=int(stats.get("stars") or 0),
        plate=f"s-tier-{slevel}",
    )


def _pw_recent(items: list[dict], mode: str) -> list[dict]:
    result = []
    for i, item in enumerate(items):
        team = int(item["team"])
        mine, theirs = (item["score1"], item["score2"]) if team == 1 else (item["score2"], item["score1"])
        outcome = "平" if mine == theirs else ("胜" if team == int(item["winTeam"]) else "负")
        rating = item["pwRating"] if mode == "pvp" and float(item.get("pwRating") or 0) else item["rating"]
        star = mode == "pvp" and _pw_s(item.get("pvpScore"))
        if star:
            # S 段每局的分数变化都是 0，只给打完后的星数；和更早一局相减，最早那局算不出
            older = items[i + 1] if i + 1 < len(items) else None
            change = int(item.get("pvpStars") or 0) - int(older.get("pvpStars") or 0) if older else None
        else:
            change = int(item.get("pvpScoreChange") or 0) if mode == "pvp" else None
        result.append({
            "map": item.get("mapName") or "",
            "outcome": outcome,
            "score": f"{mine}:{theirs}",
            "kda": f"{item['kill']}/{item['death']}/{item['assist']}",
            "rating": _f(rating),
            "time": str(item.get("startTime") or "")[5:16],
            "change": change,
            "star": star,
        })
    return result


def _pw_maps(stats: dict) -> list[dict]:
    maps = sorted(stats.get("hotMaps") or [], key=lambda m: -int(m.get("totalMatch") or 0))[:5]
    return [
        {
            "name": m.get("mapName") or m.get("map"),
            "logo": m.get("mapLogo") or "",
            "image": m.get("mapImage") or "",
            "matches": int(m.get("totalMatch") or 0),
            "wins": int(m.get("winCount") or 0),
            "win_rate": _pct(int(m.get("winCount") or 0) / max(int(m.get("totalMatch") or 0), 1)),
        }
        for m in maps
    ]


def _pw_weapons(stats: dict) -> list[dict]:
    weapons = sorted(stats.get("hotWeapons") or [], key=lambda w: -int(w.get("weaponKill") or 0))[:5]
    return [
        {
            "name": w.get("weaponName"),
            "image": w.get("weaponImage") or "",
            "kills": int(w.get("weaponKill") or 0),
            "hs_rate": _pct(int(w.get("weaponHeadShot") or 0) / max(int(w.get("weaponKill") or 0), 1)),
        }
        for w in weapons
    ]


def pvp_profile_view(stats: dict, recent: list[dict], account: dict, avatar: str) -> dict:
    count = int(stats.get("cnt") or 0)
    history = _pw_recent(recent, "pvp")
    return {
        "platform": "pw",
        "mode": "pvp",
        "mode_label": MODE_LABEL["pvp"],
        "nickname": stats.get("name") or account["nickname"],
        "avatar": avatar,
        "scope": f"{stats.get('seasonId')} 赛季 {count} 场",
        "score": _pvp_score(stats),
        "badge": stats.get("summary") or "",
        "core": [
            {"label": "完美 Rating", "value": _f(stats.get("pwRating"))},
            {"label": "Rating", "value": _f(stats.get("rating"))},
            {"label": "ADR", "value": _f(stats.get("adr"), 1)},
            {"label": "RWS", "value": _f(stats.get("rws"), 1)},
            {"label": "K/D", "value": _f(stats.get("kd"))},
            {"label": "胜率", "value": _pct(stats.get("winRate"))},
        ],
        "details": [
            {"label": "爆头率", "value": _pct(stats.get("headShotRatio"))},
            {"label": "首杀率", "value": _pct(stats.get("entryKillRatio"))},
            _group("击杀/死亡/助攻", {"击杀": stats.get("kills"), "死亡": stats.get("deaths"), "助攻": stats.get("assists")}),
            {"label": "MVP", "value": str(stats.get("mvpCount") or 0)},
            _group("2K/3K/4K/5K", {f"{n}K": stats.get(f"k{n}") for n in range(2, 6)}),
            _group("残局胜 1v1~1v5", {f"残局胜 1v{i}": stats.get(f"vs{i}") or 0 for i in range(1, 6)}),
            {"label": "1v1 胜率", "value": _pct(stats.get("vs1WinRate"))},
            {"label": "平均 WE", "value": _f(stats.get("avgWe"), 1)},
        ],
        "radar": _radar(stats),
        "trend": _sparkline(list(reversed(stats.get("historyPwRatings") or stats.get("historyRatings") or []))[-HISTORY_MATCHES:]),
        "bars": _bars(list(reversed(stats.get("historyPwRatings") or stats.get("historyRatings") or []))[-HISTORY_MATCHES:]),
        "trend_label": "近期完美 Rating",
        "trend2": _sparkline(list(reversed(stats.get("historyScores") or []))[-HISTORY_MATCHES:]),
        "trend2_label": "天梯分走势",
        "maps": _pw_maps(stats),
        "weapons": _pw_weapons(stats),
        "recent": history[:RECENT_ROWS],
        "history": history,
    }


def mm_profile_view(stats: dict, recent: list[dict], account: dict, avatar: str) -> dict:
    premier = int(stats.get("rank") or 0)
    history = _pw_recent(recent, "mm")
    return {
        "platform": "pw",
        "mode": "mm",
        "mode_label": MODE_LABEL["mm"],
        "nickname": stats.get("nickName") or account["nickname"],
        "avatar": avatar,
        "scope": f"最近 {stats.get('cnt')} 局，游戏时长 {stats.get('hours')} 小时",
        "score": _score("Premier", premier, plate=f"tier-{premier_tier(premier)}") if premier else None,
        "badge": "",
        "core": [
            {"label": "Rating", "value": _f(stats.get("rating"))},
            {"label": "ADR", "value": _f(stats.get("adr"), 1)},
            {"label": "KAST", "value": f"{stats.get('kast')}%"},
            {"label": "RWS", "value": _f(stats.get("rws"), 1)},
            {"label": "K/D", "value": _f(stats.get("kd"))},
            {"label": "胜率", "value": _pct(stats.get("winRate"))},
        ],
        "details": [
            {"label": "爆头率", "value": _pct(stats.get("headShotRatio"))},
            {"label": "首杀率", "value": _pct(stats.get("entryKillRatio"))},
            {"label": "狙杀占比", "value": _pct(stats.get("awpKillRatio"))},
            {"label": "闪光成功率", "value": _pct(stats.get("flashSuccessRatio"))},
            _group("击杀/死亡/助攻", {"击杀": stats.get("kills"), "死亡": stats.get("deaths"), "助攻": stats.get("assists")}),
            _group("3K/4K/5K", {f"{n}K": stats.get(f"k{n}") for n in range(3, 6)}),
            {"label": "残局胜利", "value": str(stats.get("endingWin") or 0)},
            {"label": "历史胜场", "value": str(stats.get("historyWinCount") or 0)},
        ],
        "radar": None,
        "trend": _sparkline(list(reversed(stats.get("historyRatings") or []))[-HISTORY_MATCHES:]),
        "bars": _bars(list(reversed(stats.get("historyRatings") or []))[-HISTORY_MATCHES:]),
        "trend_label": "近期 Rating",
        "trend2": _sparkline([v for v in reversed(stats.get("historyComprehensiveScores") or []) if v]),
        "trend2_label": "Premier 走势",
        "maps": _pw_maps(stats),
        "weapons": _pw_weapons(stats),
        "recent": history[:RECENT_ROWS],
        "history": history,
    }


def _fivee_recent(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        outcome = "平" if item.get("is_tie") else ("胜" if item.get("is_win") else "负")
        # 列表里不写本人在哪一方，按胜负把大比分放前面
        high, low = sorted([int(item["group1_all_score"]), int(item["group2_all_score"])], reverse=True)
        result.append({
            "map": item.get("map_desc") or item.get("map"),
            "outcome": outcome,
            "score": f"{low}:{high}" if outcome == "负" else f"{high}:{low}",
            "kda": f"{item['kill']}/{item['death']}",
            "rating": _f(item.get("rating")),
            "time": _ts(item["start_time"]),
            "change": round(float(item.get("change_elo") or 0)),
        })
    return result


def _fivee_detail_row(match: dict) -> dict:
    """gate 列表之外更早的几场，用单局 view 拼成和 _fivee_recent 同样的行"""
    me = match["me"]
    return {
        "map": match["map_name"],
        "outcome": match["outcome"],
        "score": match["score"],
        "kda": f"{me['k']}/{me['d']}",
        "rating": me["rating"],
        "time": match["time"],
        "change": me["score_change"],
    }


def _fivee_stars(name: str, stars) -> int | None:
    """只有 S 级段位有星数"""
    return int(stars or 0) if name.startswith("S") else None


def fivee_current(home: dict, recent: list[dict]) -> dict:
    """当前赛季。match_status 3 是定级中，这时 level_id 可能没有，有也只是暂定"""
    season = home["elo_info"]["modes"]["9"]
    level_type = int(season.get("level_type") or 2)
    if season.get("match_status") == 3 or not season.get("level_id"):
        return _score("本赛季 ELO", None, name="定级中", icon=level_icon(level_type, None))
    level_id = int(season["level_id"])
    name = level_name(level_type, level_id)
    # 当前赛季接口不带星数，读最近一局打完时的
    stars = _fivee_stars(name, (recent[0].get("level_info") or {}).get("star_num")) if recent else None
    return _score(
        "本赛季 ELO",
        round(float(season["elo"])),
        name=name,
        stars=stars,
        icon=level_icon(level_type, level_id),
        position=int(season.get("rank") or 0) or None,
    )


def _fivee_best(home: dict) -> dict | None:
    career = home["career"]
    if not career.get("level_id"):
        return None
    level_type, level_id = int(career["level_type"]), int(career["level_id"])
    name = level_name(level_type, level_id)
    return {
        "season": career.get("best_season") or "",
        "name": name,
        "stars": _fivee_stars(name, career.get("star_num")),
        "value": round(float(career.get("elo") or 0)),
        "icon": level_icon(level_type, level_id),
    }


def fivee_profile_view(
    career: dict, home: dict, recent: list[dict], arena: dict, older: list[dict], account: dict
) -> dict:
    """career 取生涯统计，home 取当前赛季和最佳赛季的段位；older 是 gate 列表之外更早几场的单局详情"""
    extra = arena["data"]
    total = int(career.get("match_total") or 0)
    history = _fivee_recent(recent) + [_fivee_detail_row(fivee_match_view(d, account["uuid"])) for d in older]
    return {
        "platform": "5e",
        "mode": "5e",
        "mode_label": MODE_LABEL["5e"],
        "nickname": account["nickname"],
        "avatar": account.get("avatar") or "",
        "account_id": account["id"],
        "scope": f"生涯 {total} 场，胜 {career.get('win_total')} 平 {career.get('tie_total')} 负 {career.get('loss_total')}",
        "score": fivee_current(home, recent),
        "best": _fivee_best(home),
        "badge": "",
        "core": [
            {"label": "Rating", "value": _f(career.get("rating"))},
            {"label": "ADR", "value": _f(career.get("adr"), 1)},
            {"label": "RWS", "value": _f(career.get("rws"), 1)},
            {"label": "KPR", "value": _f(career.get("kpr"))},
            {"label": "KAST", "value": _pct(extra.get("kast"))},
            {"label": "胜率", "value": _pct(career.get("per_win_match"))},
        ],
        "details": [
            {"label": "爆头率", "value": _pct(extra.get("per_headshot"))},
            _group("首杀/首死", {"首杀": extra.get("first_kill"), "首死": extra.get("first_death")}),
            _group("2K/3K/4K/5K", {f"{n}K": extra.get(f"kill_{n}") for n in range(2, 6)}),
            _group("残局胜 1v1~1v5", {f"残局胜 1v{i}": extra.get(f"end_1v{i}") or 0 for i in range(1, 6)}),
            _group("闪到敌人/闪到队友", {"闪到敌人": extra.get("flash_enemy"), "闪到队友": extra.get("flash_team")}),
            _group("下包/拆包", {"下包": extra.get("planted_bomb"), "拆包": extra.get("defused_bomb")}),
            {"label": "狙杀", "value": str(extra.get("awp_kill") or 0)},
            {"label": "最长连胜", "value": str(career.get("win_streak") or 0)},
        ],
        "radar": None,
        "trend": _sparkline([float(r["rating"]) for r in reversed(history)]),
        "trend_label": f"近 {len(history)} 场 Rating",
        "trend2": None,
        "trend2_label": "",
        "maps": [],
        "weapons": [],
        "recent": history[:RECENT_ROWS],
        "history": history,
    }


def _pw_hs(player: dict, mode: str) -> float:
    """爆头率：完美单局是 0.52 这种比例，官匹单局是 61.11 这种百分数"""
    value = float(player.get("headShotRatio") or 0)
    return value / 100 if mode == "mm" else value


def _ranked(players: list[dict], self_id: str) -> int:
    order = sorted(players, key=lambda p: -float(p["rating"]))
    return next(i for i, p in enumerate(order, 1) if p["id"] == self_id)


def _match_result(players: list[dict], my_team: int, score: dict, first_half: dict, winner: int) -> dict:
    """两个单局 view 共用：双方阵容（按 Rating 排序）、比分、胜负、上下半场"""
    enemy = 2 if my_team == 1 else 1
    teams = [
        {
            "label": "我方" if team == my_team else "对方",
            "score": score[team],
            "win": winner == team,
            "players": sorted((p for p in players if p["team"] == team), key=lambda p: -float(p["rating"])),
        }
        for team in (my_team, enemy)
    ]
    second_half = {team: score[team] - first_half[team] for team in (1, 2)}
    return {
        "score": f"{score[my_team]}:{score[enemy]}",
        "outcome": "平" if score[1] == score[2] else ("胜" if winner == my_team else "负"),
        "halves": (
            f"上半场 {first_half[my_team]}:{first_half[enemy]}，"
            f"下半场 {second_half[my_team]}:{second_half[enemy]}"
        ),
        "teams": teams,
    }


def pw_match_view(detail: dict, steam_id: str, mode: str) -> dict:
    base = detail["base"]
    players = []
    for p in detail["players"]:
        rating = p["pwRating"] if mode == "pvp" and float(p.get("pwRating") or 0) else p["rating"]
        players.append({
            "id": str(p["playerId"]),
            "name": p.get("nickName") or str(p["playerId"]),
            "team": int(p["team"]),
            "k": int(p["kill"]), "d": int(p["death"]), "a": int(p["assist"]),
            "adr": _f(p.get("adpr"), 0),
            "rating": _f(rating),
            "hs": _pct(_pw_hs(p, mode)),
            "fk": int(p.get("entryKill") or 0),
            "fd": int(p.get("firstDeath") or 0),
            "kast": f"{float(p['kast']):.0f}%" if mode == "mm" else None,
            "mvp": bool(p.get("mvp")),
            "score_change": int(p.get("pvpScoreChange") or 0) if mode == "pvp" else None,
            # S 段单局只给赛后星数，算不出变化，卡片直接显示星数
            "stars": int(p.get("pvpStars") or 0) if mode == "pvp" and _pw_s(p.get("pvpScore")) else None,
            "multi": f"{p.get('threeKill') or 0}/{p.get('fourKill') or 0}/{p.get('fiveKill') or 0}",
            "is_self": str(p["playerId"]) == steam_id,
            "raw": p,
        })
    me = next(p for p in players if p["is_self"])
    my_team = me["team"]
    rounds = []
    if mode == "mm" and base.get("team1round") and base.get("team2round"):
        mine = base["team1round"] if my_team == 1 else base["team2round"]
        rounds = ["win" if c != "0" else "loss" for c in mine]
    # killMap 是“这名玩家击杀了谁几次”，反查谁杀我最多
    killers = sorted(
        ((p["name"], int((p["raw"].get("killMap") or {}).get(steam_id) or 0)) for p in players if p["team"] != my_team),
        key=lambda x: -x[1],
    )
    raw = me["raw"]
    interests = [
        {"name": item.get("name"), "message": item.get("message"), "value": item.get("value")}
        for item in raw.get("csMatchPlayerInterestList") or []
    ]
    return {
        "platform": "pw",
        "mode": mode,
        "mode_label": f"{MODE_LABEL[mode]}（{base['mode']}）" if base.get("mode") else MODE_LABEL[mode],
        "map_name": base.get("map"),
        "map_logo": base.get("mapLogo") or "",
        "backdrop": base.get("mapUrl") or "",
        "time": str(base.get("startTime") or "")[:16],
        "duration": f"{base.get('duration')} 分钟",
        **_match_result(
            players,
            my_team,
            score={1: int(base["score1"]), 2: int(base["score2"])},
            first_half={1: int(base["halfScore1"]), 2: int(base["halfScore2"])},
            winner=int(base["winTeam"]),
        ),
        "rounds": rounds,
        "me": me,
        "my_rank": _ranked(players, steam_id),
        "top_killer": killers[0] if killers and killers[0][1] else None,
        "interests": interests,
        "firsts": [RANK_FIELDS.get(key, key) for key in raw.get("first") or []],
        "match_id": base["matchId"],
    }


def fivee_match_view(detail: dict, uuid: str) -> dict:
    main = detail["main"]
    players = []
    for group in (1, 2):
        for p in detail[f"group_{group}"]:
            fight = p["fight"]
            user = p["user_info"]["user_data"]
            players.append({
                "id": user["uuid"],
                "name": user.get("username") or user["uuid"][:8],
                "avatar": avatar_url((user.get("profile") or {}).get("avatarUrl") or ""),
                "team": group,
                "k": int(fight["kill"]), "d": int(fight["death"]), "a": int(fight["assist"]),
                "adr": _f(fight.get("adr"), 0),
                "rating": _f(fight.get("rating2") or fight.get("rating")),
                "hs": _pct(fight.get("per_headshot")),
                "fk": int(fight.get("first_kill") or 0),
                "fd": int(fight.get("first_death") or 0),
                "kast": _pct(fight.get("kast")),
                "mvp": str(fight.get("is_mvp")) == "1",
                "score_change": round(float((p.get("sts") or {}).get("change_elo") or 0)),
                "stars": None,
                "multi": f"{fight.get('kill_3')}/{fight.get('kill_4')}/{fight.get('kill_5')}",
                "is_self": user["uuid"] == uuid,
                "raw": fight,
            })
    me = next(p for p in players if p["is_self"])
    raw = me["raw"]
    honors = [
        label
        for key, label in (
            ("is_mvp", "MVP"), ("is_svp", "SVP"), ("is_most_first_kill", "首杀王"), ("is_most_headshot", "爆头王"),
            ("is_most_end", "残局王"), ("is_most_assist", "助攻王"), ("is_most_awp", "狙击王"), ("is_most_1v2", "1v2 最多"),
        )
        if str(raw.get(key)) == "1"
    ]
    return {
        "platform": "5e",
        "mode": "5e",
        "mode_label": MODE_LABEL["5e"],
        "map_name": main.get("map_desc") or main.get("map"),
        "map_logo": "",
        "time": _ts(main["start_time"]),
        "duration": f"{(int(main['end_time']) - int(main['start_time'])) // 60} 分钟",
        **_match_result(
            players,
            me["team"],
            score={g: int(main[f"group{g}_all_score"]) for g in (1, 2)},
            first_half={g: int(main[f"group{g}_fh_score"]) for g in (1, 2)},
            winner=int(main.get("match_winner") or 0),
        ),
        "rounds": [],
        "me": me,
        "my_rank": _ranked(players, uuid),
        "top_killer": None,
        "interests": [],
        "firsts": [],
        "honors": honors,
        "match_id": main["match_code"],
    }


async def _render(template: str, **context) -> bytes:
    context["watermark_text"] = plugin_config.watermark_text
    context["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        return await render_template(
            TEMPLATE_DIR, template, width=WIDTH, timeout=IMAGE_TIMEOUT_MS, show_images=True, **context
        )
    except PlaywrightTimeoutError:
        logger.warning(f"[cs_stats] {template} 远程图片加载超时，改为渲染无图版本")
        return await render_template(TEMPLATE_DIR, template, width=WIDTH, show_images=False, **context)


async def render_profile(view: dict, roast: dict | None = None) -> bytes:
    return await _render(f"{view['platform']}_profile.html", v=view, roast=roast)


async def render_match(view: dict, roast: dict | None = None) -> bytes:
    return await _render(f"{view['platform']}_match.html", v=view, roast=roast)


async def render_candidates(platform: str, candidates: list[dict], purpose: str) -> bytes:
    return await _render("bind_confirm.html", platform=platform, candidates=candidates, purpose=purpose)
