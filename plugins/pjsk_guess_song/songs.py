"""曲库：启动时从 masterdata 建歌曲表，加上角色缩写和别名；搜歌、解析听anvo参数、拼资源站地址"""

import difflib
import json
import random
from dataclasses import dataclass

from nonebot import logger

from plugins.pjsk_guess_song.config import ALIASES_FILE, MASTER_DIR, settings

# 听anvo 用的角色缩写，masterdata 里没有
CHARACTER_ABBRS: dict[int, str] = {
    1: "ick",
    2: "saki",
    3: "hnm",
    4: "shiho",
    5: "mnr",
    6: "hrk",
    7: "airi",
    8: "szk",
    9: "khn",
    10: "an",
    11: "akt",
    12: "toya",
    13: "tks",
    14: "emu",
    15: "nene",
    16: "rui",
    17: "knd",
    18: "mfy",
    19: "ena",
    20: "mzk",
    21: "miku",
    22: "rin",
    23: "len",
    24: "luka",
    25: "meiko",
    26: "kaito",
}
ABBR_TO_CHARACTER = {abbr: cid for cid, abbr in CHARACTER_ABBRS.items()}


@dataclass
class Vocal:
    type: str  # sekai / virtual_singer / another_vocal / original_song …
    bundle: str
    # 只记游戏角色：outside_character 的 id 与游戏角色重叠，而 another_vocal 里没有它们
    character_ids: tuple[int, ...]


@dataclass
class Song:
    id: int
    title: str
    jacket: str
    filler_sec: float
    vocals: list[Vocal]

    def vocal(self, vocal_type: str) -> Vocal | None:
        return next((v for v in self.vocals if v.type == vocal_type), None)

    def vocals_of(self, vocal_type: str) -> list[Vocal]:
        return [v for v in self.vocals if v.type == vocal_type]


songs: list[Song] = []
anvo_songs: list[Song] = []
_by_id: dict[int, Song] = {}
_full_names: dict[int, str] = {}
# 别名（小写）→ 歌曲 ID
_aliases: dict[str, int] = {}
_anvo_by_character: dict[int, list[Song]] = {}


def _read_master(name: str) -> list[dict]:
    return json.loads((MASTER_DIR / name).read_text(encoding="utf-8"))


def load() -> None:
    vocals_by_music: dict[int, list[Vocal]] = {}
    for row in _read_master("musicVocals.json"):
        vocals_by_music.setdefault(row["musicId"], []).append(
            Vocal(
                type=row["musicVocalType"],
                bundle=row["assetbundleName"],
                character_ids=tuple(
                    c["characterId"]
                    for c in row["characters"]
                    if c["characterType"] == "game_character"
                ),
            )
        )

    songs[:] = [
        Song(
            id=row["id"],
            title=row["title"],
            jacket=row["assetbundleName"],
            filler_sec=row["fillerSec"],
            vocals=vocals_by_music.get(row["id"], []),
        )
        for row in _read_master("musics.json")
    ]
    _by_id.clear()
    _by_id.update({s.id: s for s in songs})
    anvo_songs[:] = [s for s in songs if s.vocal("another_vocal")]

    _anvo_by_character.clear()
    for song in anvo_songs:
        ids = {cid for v in song.vocals_of("another_vocal") for cid in v.character_ids}
        for cid in ids:
            _anvo_by_character.setdefault(cid, []).append(song)

    _full_names.clear()
    _full_names.update(
        {
            row["id"]: (row.get("firstName") or "") + row["givenName"]
            for row in _read_master("gameCharacters.json")
        }
    )

    _aliases.clear()
    for song_id, names in json.loads(ALIASES_FILE.read_text(encoding="utf-8")).items():
        for name in names:
            _aliases[name.lower()] = int(song_id)


def stats() -> dict[str, int]:
    return {
        "songs": len(songs),
        "anvo": len(anvo_songs),
        "aliased": len({sid for sid in _aliases.values() if sid in _by_id}),
    }


def full_name(character_id: int) -> str:
    return _full_names.get(character_id, "未知")


def vocalist_name(vocal: Vocal) -> str:
    return " + ".join(full_name(cid) for cid in vocal.character_ids) or "未知"


def song_url(bundle: str) -> str:
    base = settings.asset_url_base.rstrip("/")
    return f"{base}/{settings.asset_server}-assets/ondemand/music/long/{bundle}/{bundle}.mp3"


def jacket_url(jacket: str) -> str:
    base = settings.asset_url_base.rstrip("/")
    return f"{base}/{settings.asset_server}-assets/startapp/music/jacket/{jacket}/{jacket}.png"


def find_song(query: str) -> Song | None:
    """按 ID → 别名 → 标题包含 → 模糊匹配的顺序找歌"""
    if query.isdecimal() and int(query) in _by_id:
        return _by_id[int(query)]

    query = query.lower().strip()
    if query in _aliases and _aliases[query] in _by_id:
        return _by_id[_aliases[query]]

    found = [s for s in songs if query in s.title.lower()]
    if found:
        exact = next((s for s in found if s.title.lower() == query), None)
        return exact or min(found, key=lambda s: len(s.title))

    candidates = {s.title.lower(): s.id for s in songs}
    candidates.update({a: sid for a, sid in _aliases.items() if sid in _by_id})
    matches = difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)
    if not matches:
        return None
    logger.debug(f"模糊搜索命中: '{query}' -> '{matches[0]}'")
    return _by_id[candidates[matches[0]]]


def parse_anvo(content: str) -> tuple[Song | None, Vocal | None, bool]:
    """解析听anvo参数，返回 (歌曲, 版本, 是否只列出版本)。

    - 空：随机一首的随机一个 another_vocal
    - `歌名 缩写+缩写`：该歌里角色组合完全一致的版本
    - 单个缩写：该角色参与的随机一首，优先独唱版
    - 其他：当作歌名，列出它的所有 another_vocal 版本
    """
    if not content:
        song = random.choice(anvo_songs)
        return song, random.choice(song.vocals_of("another_vocal")), False

    parts = content.rsplit(maxsplit=1)
    abbrs = parts[-1].lower().split("+")
    target = {ABBR_TO_CHARACTER.get(abbr) for abbr in abbrs}
    is_characters = None not in target

    if is_characters and len(parts) > 1:
        song = find_song(parts[0])
        if song is None:
            return None, None, False
        vocal = next(
            (
                v
                for v in song.vocals_of("another_vocal")
                if set(v.character_ids) == target
            ),
            None,
        )
        return song, vocal, False

    if is_characters and len(target) == 1:
        (cid,) = target
        if cid not in _anvo_by_character:
            return None, None, False
        song = random.choice(_anvo_by_character[cid])
        candidates = [
            v for v in song.vocals_of("another_vocal") if cid in v.character_ids
        ]
        solo = next((v for v in candidates if v.character_ids == (cid,)), None)
        return song, solo or candidates[0], False

    return find_song(content), None, True
