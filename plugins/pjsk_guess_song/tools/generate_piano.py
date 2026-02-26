import json
import os
import sys
import shutil
import warnings
import traceback
import time
import math
from pathlib import Path

# 尝试导入必要的库
try:
    import torch
    import librosa
    import pretty_midi
    import numpy as np
    from transformers import Pop2PianoForConditionalGeneration, AutoProcessor
    from midi2audio import FluidSynth
    from pydub import AudioSegment
except ImportError as e:
    print(f"缺少 Python 依赖库: {e}")
    sys.exit(1)

# ================= 配置区域 =================

BASE_DIR = Path(".")
RESOURCES_DIR = BASE_DIR / "resources"
SONGS_DIR = RESOURCES_DIR / "songs"
OUTPUT_DIR = RESOURCES_DIR / "songs_piano_trimmed_mp3"
TEMP_DIR = BASE_DIR / "temp_piano_chunks"
GUESS_SONG_JSON = RESOURCES_DIR / "guess_song.json"
ERROR_LOG_PATH = BASE_DIR / "error_log.json"

MODEL_REPO_ID = "sweetcocoa/pop2piano"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# [重要修改] 模型推荐采样率为 44100Hz
TARGET_SR = 44100
CHUNK_DURATION = 30


# ================= 辅助函数 =================

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def check_system_dependencies():
    if not shutil.which("fluidsynth"):
        log("[System] 错误: 未找到 'fluidsynth' 命令。请先安装: sudo apt install fluidsynth")
        return None

    common_sf2_paths = [
        str(RESOURCES_DIR / "piano.sf2"),
    ]

    for path in common_sf2_paths:
        if os.path.exists(path):
            log(f"[System] 找到 SoundFont: {path}")
            return path

    log("[System] 错误: 未找到 SoundFont。请安装 fluid-soundfont-gm 或将 .sf2 放入目录。")
    return None


def load_song_data():
    if not GUESS_SONG_JSON.exists():
        log(f"[Data] 错误: 找不到配置文件 {GUESS_SONG_JSON}")
        return []
    with open(GUESS_SONG_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def get_best_vocal_bundle(song_data):
    vocals = song_data.get("vocals", [])
    if not vocals: return None
    for v_type in ["sekai", "virtual_singer"]:
        for v in vocals:
            if v.get("musicVocalType") == v_type:
                return v.get("vocalAssetbundleName")
    return vocals[0].get("vocalAssetbundleName")


# ================= 核心处理逻辑 =================

def process_single_song_chunked(processor, model, sound_font, song):
    song_id = song.get('id')
    title = song.get('title', 'Unknown')

    bundle_name = get_best_vocal_bundle(song)
    if not bundle_name:
        log(f"[{title}] 跳过: 无 Vocal 信息")
        return "error", "No vocal info"

    src_audio = SONGS_DIR / bundle_name / f"{bundle_name}.mp3"
    target_dir = OUTPUT_DIR / bundle_name
    target_mp3 = target_dir / f"{bundle_name}.mp3"
    song_temp_dir = TEMP_DIR / str(song_id)

    if not src_audio.exists():
        log(f"[{title}] 错误: 源文件不存在 -> {src_audio}")
        return "error", "Source file not found"

    if target_mp3.exists():
        log(f"[{title}] 跳过: 目标文件已存在")
        return "skip", "Target exists"

    target_dir.mkdir(parents=True, exist_ok=True)
    if song_temp_dir.exists(): shutil.rmtree(song_temp_dir)
    song_temp_dir.mkdir(parents=True, exist_ok=True)

    log(f"[{title}] 开始处理 (ID: {song_id})...")

    try:
        # 1. 加载音频 (使用 44100Hz)
        log(f"  -> [Load] Reading audio (sr={TARGET_SR})...")
        y, sr = librosa.load(str(src_audio), sr=TARGET_SR)

        # [修复] 强制归一化 (防止音量太小模型不识别)
        y = librosa.util.normalize(y)

        chunk_samples = int(CHUNK_DURATION * sr)
        total_chunks = math.ceil(len(y) / chunk_samples)
        midi_parts = []

        # 2. 分段循环
        for i in range(total_chunks):
            start_idx = i * chunk_samples
            end_idx = min((i + 1) * chunk_samples, len(y))
            chunk_y = y[start_idx:end_idx]

            # 跳过过短的片段
            if len(chunk_y) < sr * 1.0: continue

            # A. 预处理
            inputs = processor(audio=chunk_y, sampling_rate=sr, return_tensors="pt")
            input_features = inputs["input_features"].to(DEVICE)

            # B. 生成
            # [关键修复] composer="composer1" (无下划线)
            # [关键修复] max_new_tokens=2048 (防止截断)
            with torch.no_grad():
                generated_tokens = model.generate(
                    input_features=input_features,
                    composer="composer1",
                    max_new_tokens=2048
                )

            # C. 解码
            gen_tokens_cpu = generated_tokens.cpu()
            inputs_cpu = {k: v.cpu() if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}

            generated_midi = processor.batch_decode(
                token_ids=gen_tokens_cpu,
                feature_extractor_output=inputs_cpu
            )["pretty_midi_objects"][0]

            part_midi_path = song_temp_dir / f"part_{i}.mid"
            generated_midi.write(str(part_midi_path))
            midi_parts.append(part_midi_path)

            # 简单检查一下生成的音符数
            note_count = len(generated_midi.instruments[0].notes) if generated_midi.instruments else 0
            log(f"     [Chunk {i + 1}] Saved MIDI. Notes: {note_count}")

        # 3. 合并 MIDI
        if not midi_parts: return "error", "No MIDI generated"

        log(f"  -> [Merge] Combining {len(midi_parts)} parts...")
        full_midi = pretty_midi.PrettyMIDI()
        piano_program = pretty_midi.instrument_name_to_program('Acoustic Grand Piano')
        full_inst = pretty_midi.Instrument(program=piano_program)

        for i, midi_path in enumerate(midi_parts):
            try:
                part = pretty_midi.PrettyMIDI(str(midi_path))
                time_offset = i * CHUNK_DURATION
                for inst in part.instruments:
                    for note in inst.notes:
                        new_note = pretty_midi.Note(
                            velocity=note.velocity,
                            pitch=note.pitch,
                            start=note.start + time_offset,
                            end=note.end + time_offset
                        )
                        full_inst.notes.append(new_note)
            except Exception as e:
                log(f"     [Merge] Warning: {e}")

        full_midi.instruments.append(full_inst)
        temp_full_midi = song_temp_dir / "full_merged.mid"
        full_midi.write(str(temp_full_midi))

        # 4. 渲染为 MP3
        log(f"  -> [Render] Synthesizing MP3...")
        temp_wav = song_temp_dir / "full.wav"
        fs = FluidSynth(sound_font)
        fs.midi_to_audio(str(temp_full_midi), str(temp_wav))

        if temp_wav.exists():
            audio = AudioSegment.from_wav(str(temp_wav))
            (audio + 3).export(str(target_mp3), format="mp3", bitrate="128k")
            log(f"     [Success] Saved to: {target_mp3}")

        # 清理
        try:
            shutil.rmtree(song_temp_dir)
        except:
            pass

        return "success", None

    except Exception as e:
        log(f"\n[ERROR][{title}] Exception:")
        traceback.print_exc()
        return "error", str(e)


# ================= 主程序 =================

def main():
    print("=== PJSK Piano Generator (Fixed Version) ===")

    sound_font = check_system_dependencies()
    if not sound_font: return

    log(f"Loading Model... (Device: {DEVICE})")
    try:
        processor = AutoProcessor.from_pretrained(MODEL_REPO_ID)
        model = Pop2PianoForConditionalGeneration.from_pretrained(MODEL_REPO_ID).to(DEVICE)
    except Exception as e:
        log(f"Model load failed: {e}")
        return

    songs = load_song_data()
    total = len(songs)
    log(f"Loaded {total} songs.")

    error_list = []
    stats = {"success": 0, "skip": 0, "error": 0}

    for i, song in enumerate(songs):
        print(f"\n--- [{i + 1}/{total}] {song.get('title')} ---")
        status, err_msg = process_single_song_chunked(processor, model, sound_font, song)
        stats[status] += 1

        if status == "error":
            error_list.append({"id": song.get("id"), "title": song.get("title"), "error": err_msg})

    log(f"Finished. Stats: {stats}")
    if error_list:
        with open(ERROR_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(error_list, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    main()