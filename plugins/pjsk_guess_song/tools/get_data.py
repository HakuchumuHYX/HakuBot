import os
import json
import random
import time
import requests

# --- 配置区域 ---
# JSON文件路径 (默认为当前目录下的 guess_song.json)
JSON_PATH = 'resources/guess_song.json'

# 下载保存的根目录 (将创建 resources 文件夹)
SAVE_ROOT = 'resources'

# 定义数据源模板
# {name} 会被替换为 assetbundleName
# 不提供具体的链接，请自行查找
SOURCES = {
    "sekai": {
        "jacket": "https://xxx/sekai-jp-assets/music/jacket/{name}/{name}.png",
        "mp3": "https://xxx/sekai-jp-assets/music/long/{name}/{name}.mp3"
    },
    "haruki": {
        "jacket": "https://xxx/jp-assets/startapp/music/jacket/{name}/{name}.png",
        "mp3": "https://xxx/jp-assets/ondemand/music/long/{name}/{name}.mp3"
    }
}


def random_sleep():
    """随机休眠，防止请求过快"""
    sleep_time = random.uniform(0.5, 2.0)
    time.sleep(sleep_time)


def download_file(url, save_path):
    """通用下载函数"""
    if os.path.exists(save_path):
        print(f"[跳过] 文件已存在: {save_path}")
        return

    # 确保目录存在
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    # 随机选择 User-Agent
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        print(f"[下载中] ({url}) -> {save_path}")
        # 随机延迟
        random_sleep()

        response = requests.get(url, headers=headers, stream=True, timeout=15)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"[成功] 已保存")
        else:
            print(f"[失败] HTTP {response.status_code}: {url}")
            if os.path.exists(save_path) and os.path.getsize(save_path) == 0:
                os.remove(save_path)

    except Exception as e:
        print(f"[错误] {e}")


def main():
    if not os.path.exists(JSON_PATH):
        print(f"错误: 找不到文件 {JSON_PATH}")
        return

    print(f"正在读取 {JSON_PATH} ...")
    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        songs = json.load(f)

    total_songs = len(songs)
    print(f"共加载 {total_songs} 首歌曲信息，开始处理资源...")

    for index, song in enumerate(songs):
        song_id = song.get('id')
        title = song.get('title', '未知歌曲')
        print(f"\n=== 处理第 {index + 1}/{total_songs} 首: {song_id}. {title} ===")

        # 1. 下载封面 (Jacket)
        jacket_name = song.get('jacketAssetbundleName')
        if jacket_name:
            # 随机选择源
            source_key = random.choice(list(SOURCES.keys()))
            url_template = SOURCES[source_key]["jacket"]
            url = url_template.format(name=jacket_name)

            # 插件要求的路径: resources/music_jacket/{name}.png
            save_path = os.path.join(SAVE_ROOT, "music_jacket", f"{jacket_name}.png")
            download_file(url, save_path)

        # 2. 下载音频 (Vocals/MP3)
        vocals = song.get('vocals', [])
        for vocal in vocals:
            vocal_name = vocal.get('vocalAssetbundleName')
            if not vocal_name:
                continue

            # 随机选择源 (每个文件独立随机)
            source_key = random.choice(list(SOURCES.keys()))
            url_template = SOURCES[source_key]["mp3"]
            url = url_template.format(name=vocal_name)

            # 插件要求的路径: resources/songs/{bundle_name}/{bundle_name}.mp3
            # 注意：插件的路径结构是 文件夹/文件名.mp3
            save_path = os.path.join(SAVE_ROOT, "songs", vocal_name, f"{vocal_name}.mp3")
            download_file(url, save_path)

    print("\n所有任务处理完成！")


if __name__ == "__main__":
    # 检查requests库
    try:
        import requests
    except ImportError:
        print("请先安装 requests 库: pip install requests")
    else:
        main()