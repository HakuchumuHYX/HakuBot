import os
import shutil
import subprocess
import sys
import json
from pathlib import Path
from pydub import AudioSegment

# --- 配置 ---
JSON_PATH = 'resources/guess_song.json'
RESOURCES_DIR = Path("resources")
INPUT_DIR = RESOURCES_DIR / "songs"

# 输出目录
OUTPUT_DIRS = {
    "vocals": RESOURCES_DIR / "vocals_only",
    "drums": RESOURCES_DIR / "drums_only",
    "bass": RESOURCES_DIR / "bass_only",
    "accompaniment": RESOURCES_DIR / "accompaniment"
}

MODEL_NAME = "htdemucs"
# 批处理大小：一次让 Demucs 处理多少首歌
# 建议 10-20，设置太大可能会超过 Windows 命令行长度限制
BATCH_SIZE = 1


def ensure_dirs():
    for d in OUTPUT_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)


def is_processed(bundle_name):
    for dir_path in OUTPUT_DIRS.values():
        if not (dir_path / f"{bundle_name}.mp3").exists():
            return False
    return True


def get_target_bundles():
    """读取 JSON 并筛选最佳版本"""
    if not os.path.exists(JSON_PATH):
        print(f"错误: 找不到 {JSON_PATH}")
        sys.exit(1)

    with open(JSON_PATH, 'r', encoding='utf-8') as f:
        songs = json.load(f)

    targets = []

    print(f"正在筛选最佳版本...")
    for song in songs:
        vocals = song.get('vocals', [])
        if not vocals: continue

        # 筛选策略: Sekai > VS/Original > First
        chosen_vocal = None
        for v in vocals:
            if v.get('musicVocalType') == 'sekai':
                chosen_vocal = v
                break
        if not chosen_vocal:
            for v in vocals:
                ctype = v.get('musicVocalType')
                if ctype == 'virtual_singer' or ctype == 'original_song':
                    chosen_vocal = v
                    break
        if not chosen_vocal and vocals:
            chosen_vocal = vocals[0]

        if chosen_vocal:
            bundle_name = chosen_vocal.get('vocalAssetbundleName')
            src_path = INPUT_DIR / bundle_name / f"{bundle_name}.mp3"
            if src_path.exists():
                # 只有未处理的才加入列表
                if not is_processed(bundle_name):
                    targets.append({
                        'title': song.get('title'),
                        'bundle': bundle_name,
                        'path': src_path
                    })

    print(f"筛选完成！待处理: {len(targets)} 首")
    return targets


def mix_and_move(bundle_name):
    """后处理：合成伴奏并移动文件"""
    try:
        # Demucs 输出在 temp_demucs/model/bundle_name/...
        base_dir = Path("temp_demucs") / MODEL_NAME / bundle_name

        if not base_dir.exists():
            print(f"  [警告] 未找到输出目录: {bundle_name}")
            return

        # 1. 合成伴奏
        try:
            drums = AudioSegment.from_mp3(base_dir / "drums.mp3")
            bass = AudioSegment.from_mp3(base_dir / "bass.mp3")
            other = AudioSegment.from_mp3(base_dir / "other.mp3")
            accompaniment = drums.overlay(bass).overlay(other)

            acc_path = OUTPUT_DIRS["accompaniment"] / f"{bundle_name}.mp3"
            accompaniment.export(acc_path, format="mp3", bitrate="128k")
        except Exception as e:
            print(f"  [合成失败] {bundle_name}: {e}")

        # 2. 移动单轨
        mappings = {
            "vocals.mp3": OUTPUT_DIRS["vocals"],
            "drums.mp3": OUTPUT_DIRS["drums"],
            "bass.mp3": OUTPUT_DIRS["bass"]
        }
        for fname, dest_dir in mappings.items():
            src = base_dir / fname
            if src.exists():
                shutil.move(str(src), str(dest_dir / f"{bundle_name}.mp3"))

    except Exception as e:
        print(f"  [后处理错误] {e}")


def process_batch(batch_targets):
    """处理一批歌曲"""
    # 提取所有文件路径
    input_paths = [str(t['path']) for t in batch_targets]

    # 构建一个包含多个文件的命令
    cmd = [
              sys.executable, "-m", "demucs",
              "-n", MODEL_NAME,
              "--segment", "5",  # 保持显存优化
              "--mp3",
              "--mp3-bitrate", "128",
              "-o", "temp_demucs"
          ] + input_paths  # <--- 关键：一次传入多个文件

    print(f"\n>>> 正在启动 Demucs 处理本批次 ({len(input_paths)} 首)...")
    try:
        # 调用一次 Demucs 处理这一批
        subprocess.run(cmd, check=True)

        # Demucs 处理完这批后，逐个进行后处理（移动、合成）
        print(">>> 正在进行后处理 (合成伴奏/移动文件)...")
        for t in batch_targets:
            mix_and_move(t['bundle'])

        # 清理临时目录 (防止堆积)
        if Path("temp_demucs").exists():
            shutil.rmtree("temp_demucs")

    except subprocess.CalledProcessError as e:
        print(f"  [Demucs崩溃] 批次处理失败 Code: {e.returncode}")
    except Exception as e:
        print(f"  [错误] {e}")


def main():
    ensure_dirs()
    all_targets = get_target_bundles()

    if not all_targets:
        print("没有需要处理的歌曲。")
        return

    total = len(all_targets)

    # 分批处理
    for i in range(0, total, BATCH_SIZE):
        batch = all_targets[i: i + BATCH_SIZE]
        print(f"\n=== 批次进度: {i + 1}-{min(i + BATCH_SIZE, total)} / {total} ===")
        process_batch(batch)

    print("\n所有分轨任务完成！")


if __name__ == "__main__":
    main()
