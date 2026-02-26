import json
import os
from collections import defaultdict
from pathlib import Path
from nonebot.log import logger

# 默认源文件目录（独立运行时使用）
DEFAULT_SOURCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "haruki-sekai-master", "master"))


def _load_json(source_dir: str, filename: str):
    """从指定目录加载JSON文件"""
    file_path = os.path.join(source_dir, filename)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"[generate_guess_song] 找不到文件 {file_path}")
        return []
    except json.JSONDecodeError:
        logger.warning(f"[generate_guess_song] 文件 {file_path} 解析失败")
        return []


def generate(source_dir: str, output_path: str) -> bool:
    """
    从 masterdata 生成 guess_song.json。

    :param source_dir: masterdata 目录路径（包含 musics.json 等）
    :param output_path: 输出文件的完整路径
    :return: 是否成功
    """
    logger.info(f"[generate_guess_song] 从 {source_dir} 读取数据...")

    musics_data = _load_json(source_dir, 'musics.json')
    music_tags_data = _load_json(source_dir, 'musicTags.json')
    music_vocals_data = _load_json(source_dir, 'musicVocals.json')

    if not musics_data:
        logger.error("[generate_guess_song] 未能读取到歌曲数据。")
        return False

    # 预处理 musicTags
    tags_map = defaultdict(list)
    for tag_entry in music_tags_data:
        m_id = tag_entry.get('musicId')
        tag = tag_entry.get('musicTag')
        if m_id is not None and tag:
            tags_map[m_id].append(tag)

    # 预处理 musicVocals
    vocals_map = defaultdict(list)
    for vocal_entry in music_vocals_data:
        m_id = vocal_entry.get('musicId')
        if m_id is None:
            continue
        raw_chars = vocal_entry.get('characters', [])
        cleaned_chars = [{"characterId": c.get('characterId'), "characterType": c.get('characterType')} for c in raw_chars]
        cleaned_vocal = {
            "musicVocalType": vocal_entry.get('musicVocalType'),
            "caption": vocal_entry.get('caption'),
            "vocalAssetbundleName": vocal_entry.get('assetbundleName'),
            "characters": cleaned_chars
        }
        vocals_map[m_id].append(cleaned_vocal)

    # 合成
    guess_song_list = []
    for music in musics_data:
        m_id = music.get('id')
        song_obj = {
            "id": m_id,
            "title": music.get('title'),
            "jacketAssetbundleName": music.get('assetbundleName'),
            "liveTalkBackgroundAssetbundleName": music.get('liveTalkBackgroundAssetbundleName'),
            "fillerSec": music.get('fillerSec'),
            "musicTags": tags_map.get(m_id, []),
            "vocals": vocals_map.get(m_id, [])
        }
        guess_song_list.append(song_obj)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(guess_song_list, f, indent=2, ensure_ascii=False)

    logger.info(f"[generate_guess_song] 成功生成 {output_path}，共 {len(guess_song_list)} 首歌曲。")
    return True


def main():
    """独立运行入口"""
    print(f"正在从以下目录读取数据: {DEFAULT_SOURCE_DIR}")
    success = generate(DEFAULT_SOURCE_DIR, 'guess_song.json')
    if success:
        print("生成完成。")
    else:
        print("生成失败。")


if __name__ == "__main__":
    main()
