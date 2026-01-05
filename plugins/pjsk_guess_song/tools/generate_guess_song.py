import json
import os
from collections import defaultdict

# 定义源文件所在的绝对路径
SOURCE_DIR = r"E:\Download\bot\haruki-sekai-master\master"


def load_json(filename):
    """从指定目录加载JSON文件"""
    # 拼接完整路径
    file_path = os.path.join(SOURCE_DIR, filename)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"警告: 找不到文件 {file_path}")
        return []
    except json.JSONDecodeError:
        print(f"警告: 文件 {file_path} 解析失败")
        return []


def main():
    print(f"正在从以下目录读取数据: {SOURCE_DIR}")

    # 1. 加载源文件 (自动使用上面定义的 SOURCE_DIR)
    musics_data = load_json('musics.json')
    music_tags_data = load_json('musicTags.json')
    music_vocals_data = load_json('musicVocals.json')

    if not musics_data:
        print("错误: 未能读取到歌曲数据，程序终止。")
        return

    # 2. 预处理 musicTags (按 musicId 分组)
    print("正在处理标签数据...")
    tags_map = defaultdict(list)
    for tag_entry in music_tags_data:
        m_id = tag_entry.get('musicId')
        tag = tag_entry.get('musicTag')
        if m_id is not None and tag:
            tags_map[m_id].append(tag)

    # 3. 预处理 musicVocals (按 musicId 分组)
    print("正在处理音频版本数据...")
    vocals_map = defaultdict(list)
    for vocal_entry in music_vocals_data:
        m_id = vocal_entry.get('musicId')
        if m_id is None:
            continue

        # 提取并重构 characters 列表
        raw_chars = vocal_entry.get('characters', [])
        cleaned_chars = []
        for char in raw_chars:
            cleaned_chars.append({
                "characterId": char.get('characterId'),
                "characterType": char.get('characterType')
            })

        # 构建目标 vocal 对象
        cleaned_vocal = {
            "musicVocalType": vocal_entry.get('musicVocalType'),
            "caption": vocal_entry.get('caption'),
            "vocalAssetbundleName": vocal_entry.get('assetbundleName'),
            "characters": cleaned_chars
        }

        vocals_map[m_id].append(cleaned_vocal)

    # 4. 合成 guess_song.json
    print("正在合成 guess_song.json ...")
    guess_song_list = []

    for music in musics_data:
        m_id = music.get('id')

        # 构建目标歌曲对象
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

    # 5. 输出结果 (保存在当前脚本运行的目录下)
    output_filename = 'guess_song.json'
    # 如果你想强制保存到 resources 目录，可以使用下面这行（可选）：
    # output_filename = os.path.join(r"E:\Download\bot\HakuBot\data\pjsk_guess_song\resources", 'guess_song.json')

    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(guess_song_list, f, indent=2, ensure_ascii=False)

    print(f"成功生成 {output_filename}，共包含 {len(guess_song_list)} 首歌曲。")


if __name__ == "__main__":
    main()