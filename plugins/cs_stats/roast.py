"""LLM 锐评：生涯和单局各一套提示词，输入取自卡片 view。

派生量（走势、最强最弱图、武器占比、排名）在这里算好再给 LLM，不让它自己做算术。
"""

import json

import json_repair
from nonebot import logger

from plugins.cs_stats.config import plugin_config
from utils.llm.client import LLMClientConfig, chat_completion
from utils.logging import get_exc_desc

PERSONA = """你在一个 CS 玩家群里，是那个打了很多年、眼光毒、嘴更毒的老哥。群友把一位玩家的{scope}甩给你，让你锐评。这是群聊里整活，不是写战绩分析报告。

怎么说：
- 看完数据，挑一两个最好笑、最离谱的点使劲打，其余的不提。数字只在它本身就是笑点时才报，整段最多两三个，别一项项念。
- 放开了玩：比喻、夸张、反讽、阴阳怪气、脑补场面、CS 圈和网上的梗都行。句子短，带情绪，像在群里接话。
- 用玩家的说法（白给、干拉、补枪、架点、混分、躺赢、下饭之类），说人话，别像教练复盘。
- 不要面面俱到，不要先夸后损再总结的套路，结尾不给建议、不说教。
- 真打得好就换个花样吹，吹也要有梗。

底线：
- 只损打法和数据；不碰外貌、家庭、地域、性别、性格，不说脏话。
- 怎么夸张都行，但数据、排名、回合经过、投掷物数量这类事实只用给出的，没给的不编。
- 不要 emoji，不要 markdown。
- title 是 6～14 个字的外号，概括{title_scope}，要像群友会脱口叫出来的。
{focus}

只输出 JSON：{{"title": "...", "roast": "..."}}"""

CAREER_FOCUS = "- roast 80～160 字，讲这人长期是个什么样的玩家：打法、偏好、最近的状态。"
MATCH_FOCUS = (
    "- roast 60～120 字，只讲这一局：carry 还是白给，有没有戏剧性的地方"
    "（被同一个人反复击杀、翻盘或被翻盘之类）。"
)

CAREER_PROMPT = PERSONA.format(scope="生涯战绩", title_scope="这个人的打法", focus=CAREER_FOCUS)
MATCH_PROMPT = PERSONA.format(scope="某一局比赛", title_scope="他在这局的表现", focus=MATCH_FOCUS)

# 闪到队友多在 CS 里很正常（人闪同出、给队友前顶），单看次数分不出是配合还是乱扔，拿来锐评只会显得不懂。
# 卡片照常显示，只是不给 LLM；后一条是完美官匹趣味标签里的同义项（按 message 匹配）
NOT_FOR_ROAST = {"闪到队友", "闪光弹致盲队友次数"}


def _trend_text(trend: dict | None, label: str) -> str | None:
    if not trend:
        return None
    return (
        f"{label}：近 {trend['count']} 个点从 {trend['first']:g} 到 {trend['last']:g}，"
        f"最低 {trend['low']:g}，最高 {trend['high']:g}"
    )


def _map_extremes(maps: list[dict]) -> str | None:
    played = [m for m in maps if m["matches"] >= 3]
    if len(played) < 2:
        return None
    by_rate = sorted(played, key=lambda m: float(m["win_rate"].rstrip("%")))
    worst, best = by_rate[0], by_rate[-1]
    return f"最强图 {best['name']}（{best['matches']} 场胜率 {best['win_rate']}），最弱图 {worst['name']}（{worst['matches']} 场胜率 {worst['win_rate']}）"


def _weapon_share(weapons: list[dict]) -> str | None:
    total = sum(w["kills"] for w in weapons)
    if not total:
        return None
    return "、".join(f"{w['name']} 占 {w['kills'] / total:.0%}" for w in weapons[:3]) + "（占常用武器总击杀）"


def _expand(items: list[dict]) -> dict:
    """卡片上「首杀/首死 73/76」这种合并写法拆成单独的键，LLM 读合并值容易张冠李戴"""
    result = {}
    for item in items:
        labels, values = item["label"].split("/"), str(item["value"]).split("/")
        if len(labels) > 1 and len(labels) == len(values):
            result.update(zip(labels, values))
        elif "1v1~1v5" in item["label"] and len(values) == 5:
            prefix = item["label"].replace("1v1~1v5", "").strip()
            result.update({f"{prefix} 1v{i}": v for i, v in enumerate(values, 1)})
        else:
            result[item["label"]] = item["value"]
    return {k: v for k, v in result.items() if k not in NOT_FOR_ROAST}


def career_input(view: dict) -> dict:
    data = {
        "模式": view["mode_label"],
        "统计范围": view["scope"],
        "核心数据": {item["label"]: item["value"] for item in view["core"]},
        "细项": _expand(view["details"]),
    }
    if view["score"]:
        data[view["score"]["label"]] = view["score"]["value"]
    if view["badge"]:
        data["平台给的标签"] = view["badge"]
    if view["radar"]:
        data["完美五维评分（满分 10）"] = {axis["label"]: axis["value"] for axis in view["radar"]["axes"]}
    if view["maps"]:
        data["常用地图"] = [f"{m['name']} {m['matches']} 场 胜率 {m['win_rate']}" for m in view["maps"]]
    if view["weapons"]:
        data["常用武器"] = [f"{w['name']} {w['kills']} 杀 爆头率 {w['hs_rate']}" for w in view["weapons"]]
    if view["recent"]:
        data["最近对局（新→旧）"] = [
            f"{r['map']} {r['outcome']} 比分 {r['score']} 击杀/死亡(/助攻) {r['kda']} Rating {r['rating']}" for r in view["recent"]
        ]
    derived = [
        _trend_text(view["trend"], view["trend_label"]),
        _trend_text(view["trend2"], view["trend2_label"]),
        _map_extremes(view["maps"]),
        _weapon_share(view["weapons"]),
    ]
    data["派生"] = [d for d in derived if d]
    return data


def _player_line(p: dict) -> str:
    return f"{p['name']}：击杀 {p['k']} 死亡 {p['d']} 助攻 {p['a']} ADR {p['adr']} Rating {p['rating']}"


def match_input(view: dict) -> dict:
    me = view["me"]
    mine, theirs = view["teams"]
    data = {
        "模式": view["mode_label"],
        "地图": view["map_name"],
        "结果": f"{view['outcome']} {view['score']}（{view['halves']}）",
        "本人": {
            "击杀": me["k"],
            "死亡": me["d"],
            "助攻": me["a"],
            "ADR": me["adr"],
            "Rating": me["rating"],
            "爆头率": me["hs"],
            "首杀": me["fk"],
            "首死": me["fd"],
            **dict(zip(("3K", "4K", "5K"), me["multi"].split("/"))),
        },
        "本人 Rating 在 10 人中排第": view["my_rank"],
        "队友": [_player_line(p) for p in mine["players"] if not p["is_self"]],
        "对手": [_player_line(p) for p in theirs["players"]],
    }
    if me["kast"]:
        data["本人"]["KAST"] = me["kast"]
    if me["score_change"] is not None:
        data["本人"]["分数变化"] = me["score_change"]
    if view["top_killer"]:
        data["杀他最多的对手"] = f"{view['top_killer'][0]}，杀了他 {view['top_killer'][1]} 次"
    if view["rounds"]:
        data["逐回合（本方视角）"] = "".join("胜" if r == "win" else "负" for r in view["rounds"])
    firsts = [f for f in view["firsts"] if f not in NOT_FOR_ROAST]
    if firsts:
        data["本局在队内排第一的指标"] = firsts
    interests = [i for i in view["interests"] if i["message"] not in NOT_FOR_ROAST]
    if interests:
        data["平台趣味标签"] = [f"{i['name']}（{i['message']} {i['value']}）" for i in interests]
    if view.get("honors"):
        data["平台荣誉"] = view["honors"]
    return data


async def generate(kind: str, view: dict) -> dict | None:
    """kind 是 career 或 match；失败返回 None，卡片就不带锐评"""
    system, payload = (
        (CAREER_PROMPT, career_input(view)) if kind == "career" else (MATCH_PROMPT, match_input(view))
    )
    try:
        result = await chat_completion(
            LLMClientConfig(**plugin_config.llm.model_dump()),
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)},
            ],
        )
    except Exception as exc:
        logger.warning(f"[cs_stats] 锐评生成失败: {get_exc_desc(exc)}")
        return None
    parsed = json_repair.loads(result.content or "")
    if not isinstance(parsed, dict) or not str(parsed.get("title") or "").strip() or not str(parsed.get("roast") or "").strip():
        logger.warning(f"[cs_stats] 锐评输出格式不对: {(result.content or '')[:200]}")
        return None
    return {"title": str(parsed["title"]).strip(), "roast": str(parsed["roast"]).strip()}
