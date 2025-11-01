# pjsk_guess_song/services/game_service.py
"""
(新文件 - 原 audio_service.py)
游戏逻辑服务
负责游戏模式定义、歌曲选择、效果组合等业务逻辑。
"""

import asyncio
import random
import time
import itertools
from typing import List, Dict, Optional, Tuple, Union
from pathlib import Path
from functools import partial
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from nonebot.log import logger
from ..config import PluginConfig
from .cache_service import CacheService
from .audio_processor import AudioProcessor


class GameService:
    def __init__(self, cache_service: CacheService, config: PluginConfig, audio_processor: AudioProcessor,
                 plugin_version: str):
        self.cache_service = cache_service
        self.config = config
        self.audio_processor = audio_processor  # 依赖注入
        self.plugin_version = plugin_version

        # (注意) output_dir 和 executor 现在从 audio_processor 间接访问或不再需要
        self.output_dir = self.audio_processor.output_dir
        self.executor = self.audio_processor.executor

        # --- 游戏模式定义 ---
        self.vocals_silence_detection = True
        self.silence_threshold_dbfs = -35

        self.game_effects = {
            'speed_2x': {'name': '2倍速', 'score': 1, 'kwargs': {'speed_multiplier': 2.0}},
            'reverse': {'name': '倒放', 'score': 3, 'kwargs': {'reverse_audio': True}},
            'piano': {'name': '钢琴', 'score': 2, 'kwargs': {'melody_to_piano': True}},
            'acc': {'name': '伴奏', 'score': 1, 'kwargs': {'play_preprocessed': 'accompaniment'}},
            'bass': {'name': '纯贝斯', 'score': 3, 'kwargs': {'play_preprocessed': 'bass_only'}},
            'drums': {'name': '纯鼓组', 'score': 4, 'kwargs': {'play_preprocessed': 'drums_only'}},
            'vocals': {'name': '纯人声', 'score': 1, 'kwargs': {'play_preprocessed': 'vocals_only'}},
        }
        self.game_modes = {
            'normal': {'name': '普通', 'kwargs': {}, 'score': 1},
            '1': {'name': '2倍速', 'kwargs': {'speed_multiplier': 2.0}, 'score': 1},
            '2': {'name': '倒放', 'kwargs': {'reverse_audio': True}, 'score': 3},
            '3': {'name': 'AI-Assisted Twin Piano ver.', 'kwargs': {'melody_to_piano': True}, 'score': 2},
            '4': {'name': '纯伴奏', 'kwargs': {'play_preprocessed': 'accompaniment'}, 'score': 1},
            '5': {'name': '纯贝斯', 'kwargs': {'play_preprocessed': 'bass_only'}, 'score': 3},
            '6': {'name': '纯鼓组', 'kwargs': {'play_preprocessed': 'drums_only'}, 'score': 4},
            '7': {'name': '纯人声', 'kwargs': {'play_preprocessed': 'vocals_only'}, 'score': 1},
        }
        self.listen_modes = {
            "piano": {"name": "钢琴", "list_attr": "available_piano_songs", "file_key": "piano",
                      "not_found_msg": "......抱歉，没有找到任何预生成的钢琴曲。",
                      "no_match_msg": "......没有找到与 '{search_term}' 匹配的歌曲，或者该歌曲没有可用的钢琴版本。",
                      "title_suffix": "(钢琴)", "is_piano": True},
            "accompaniment": {"name": "伴奏", "list_attr": "available_accompaniment_songs", "file_key": "accompaniment",
                              "not_found_msg": "......抱歉，没有找到任何预生成的伴奏曲。",
                              "no_match_msg": "......没有找到与 '{search_term}' 匹配的歌曲，或者该歌曲没有可用的伴奏版本。",
                              "title_suffix": "(伴奏)", "is_piano": False},
            "vocals": {"name": "人声", "list_attr": "available_vocals_songs", "file_key": "vocals_only",
                       "not_found_msg": "......抱歉，没有找到任何预生成的纯人声曲。",
                       "no_match_msg": "......没有找到与 '{search_term}' 匹配的歌曲，或者该歌曲没有可用的人声版本。",
                       "title_suffix": "(人声)", "is_piano": False},
            "bass": {"name": "贝斯", "list_attr": "available_bass_songs", "file_key": "bass_only",
                     "not_found_msg": "......抱歉，没有找到任何预生成的纯贝斯曲。",
                     "no_match_msg": "......没有找到与 '{search_term}' 匹配的歌曲，或者该歌曲没有可用的贝斯版本。",
                     "title_suffix": "(贝斯)", "is_piano": False},
            "drums": {"name": "鼓组", "list_attr": "available_drums_songs", "file_key": "drums_only",
                      "not_found_msg": "......抱歉，没有找到任何预生成的纯鼓点曲。",
                      "no_match_msg": "......没有找到与 '{search_term}' 匹配的歌曲，或者该歌曲没有可用的鼓点版本。",
                      "title_suffix": "(鼓组)", "is_piano": False},
        }
        self.mode_name_map = {}
        for key, value in self.game_modes.items():
            self.mode_name_map[key] = key
            self.mode_name_map[value['name'].lower()] = key
        for key, value in self.game_effects.items():
            self.mode_name_map[key] = key
            self.mode_name_map[value['name'].lower()] = key

        self.random_mode_decay_factor = self.config.random_mode_decay_factor

        self.base_effects = [
            {'name': '2倍速', 'kwargs': {'speed_multiplier': 2.0}, 'group': 'speed', 'score': 1},
            {'name': '倒放', 'kwargs': {'reverse_audio': True}, 'group': 'direction', 'score': 3},
        ]
        self.source_effects = [
            {'name': 'Twin Piano ver.', 'kwargs': {'melody_to_piano': True}, 'group': 'source', 'score': 2},
            {'name': '纯人声', 'kwargs': {'play_preprocessed': 'vocals_only'}, 'group': 'source', 'score': 1},
            {'name': '纯贝斯', 'kwargs': {'play_preprocessed': 'bass_only'}, 'group': 'source', 'score': 3},
            {'name': '纯鼓组', 'kwargs': {'play_preprocessed': 'drums_only'}, 'group': 'source', 'score': 4},
            {'name': '纯伴奏', 'kwargs': {'play_preprocessed': 'accompaniment'}, 'group': 'source', 'score': 1}
        ]

    async def get_game_clip(self, **kwargs) -> Optional[Dict]:
        """
        准备一轮新游戏。
        [重构] 此函数现在调用 self.audio_processor 来执行实际的音频处理。
        """
        if not self.cache_service.song_data:
            logger.error("无法开始游戏: 歌曲数据未加载。")
            return None

        MAX_SONG_RETRIES = 3
        MAX_SEGMENT_RETRIES_PER_SONG = 3

        preprocessed_mode = kwargs.get("play_preprocessed")
        is_piano_mode = kwargs.get("melody_to_piano", False)
        loop = asyncio.get_running_loop()

        song = kwargs.get("force_song_object")
        audio_source = None
        forced_start_ms = None

        # (此选择逻辑保持不变)
        for song_attempt in range(MAX_SONG_RETRIES):
            if not song:
                if preprocessed_mode:
                    available_bundles = self.cache_service.preprocessed_tracks.get(preprocessed_mode, set())
                    if not available_bundles:
                        logger.error(f"无法开始 {preprocessed_mode} 模式: 没有找到任何预处理的音轨文件。")
                        return None
                    chosen_bundle = random.choice(list(available_bundles))
                    song = self.cache_service.bundle_to_song_map.get(chosen_bundle)
                elif is_piano_mode:
                    if not self.cache_service.available_piano_songs:
                        logger.error("无法开始钢琴模式: 没有找到任何预生成的钢琴曲。")
                        return None
                    song = random.choice(self.cache_service.available_piano_songs)
                else:
                    song = random.choice(self.cache_service.song_data)

            if not song:
                logger.error("在游戏准备的步骤一中未能确定歌曲。")
                return None

            logger.debug(f"歌曲尝试 {song_attempt + 1}/{MAX_SONG_RETRIES}: 选择歌曲 '{song.get('title')}'")

            vocal_version = kwargs.get("force_vocal_version")
            if preprocessed_mode:
                possible_bundles = [v['vocalAssetbundleName'] for v in song.get('vocals', []) if
                                    v['vocalAssetbundleName'] in self.cache_service.preprocessed_tracks.get(
                                        preprocessed_mode, set())]
                if not possible_bundles:
                    audio_source = None
                else:
                    chosen_bundle = random.choice(possible_bundles)
                    audio_source = self.cache_service.get_resource_path_or_url(
                        f"{preprocessed_mode}/{chosen_bundle}.mp3")
            elif is_piano_mode:
                all_song_bundles = {v['vocalAssetbundleName'] for v in song.get('vocals', [])}
                valid_piano_bundles = list(
                    all_song_bundles.intersection(self.cache_service.available_piano_songs_bundles))
                if not valid_piano_bundles:
                    audio_source = None
                else:
                    chosen_bundle = random.choice(valid_piano_bundles)
                    audio_source = self.cache_service.get_resource_path_or_url(
                        f"songs_piano_trimmed_mp3/{chosen_bundle}/{chosen_bundle}.mp3")
            else:
                if not vocal_version:
                    sekai_ver = next((v for v in song.get('vocals', []) if v.get('musicVocalType') == 'sekai'), None)
                    vocal_version = sekai_ver if sekai_ver else (
                        random.choice(song.get("vocals", [])) if song.get("vocals") else None)
                if vocal_version:
                    bundle_name = vocal_version["vocalAssetbundleName"]
                    audio_source = self.cache_service.get_resource_path_or_url(f"songs/{bundle_name}/{bundle_name}.mp3")
                else:
                    audio_source = None

            if not audio_source:
                logger.warning(f"歌曲 '{song.get('title')}' 没有有效的音频源文件，尝试下一首。")
                song = None
                continue

            if self.vocals_silence_detection and preprocessed_mode == 'vocals_only':
                try:
                    target_duration_s = self.config.clip_duration_seconds
                    # [重构] 调用 audio_processor
                    total_duration_ms = await loop.run_in_executor(self.executor,
                                                                   self.audio_processor.get_duration_ms_ffprobe_sync,
                                                                   audio_source)

                    if total_duration_ms is None: raise ValueError("ffprobe failed")

                    is_segment_found = False
                    for segment_attempt in range(MAX_SEGMENT_RETRIES_PER_SONG):
                        start_range_min = int(song.get("fillerSec", 0) * 1000)
                        start_range_max = int(total_duration_ms - (target_duration_s * 1000))
                        random_start_s = (random.randint(start_range_min,
                                                         start_range_max) if start_range_min < start_range_max else start_range_min) / 1000.0

                        # [重构] 调用 audio_processor
                        mean_dbfs = await self.audio_processor.get_segment_mean_dbfs_ffmpeg(audio_source,
                                                                                            random_start_s,
                                                                                            target_duration_s)

                        if mean_dbfs is not None and mean_dbfs > self.silence_threshold_dbfs:
                            logger.debug(
                                f"片段尝试 {segment_attempt + 1}: 找到有效人声片段 (响度: {mean_dbfs:.2f} dBFS)。")
                            forced_start_ms = int(random_start_s * 1000)
                            is_segment_found = True
                            break
                        else:
                            logger.debug(
                                f"片段尝试 {segment_attempt + 1}: 人声片段过静 (响度: {mean_dbfs or -999.0:.2f} dBFS)，重试。")

                    if is_segment_found:
                        break
                    else:
                        logger.warning(
                            f"歌曲 '{song.get('title')}' 在 {MAX_SEGMENT_RETRIES_PER_SONG} 次尝试后未找到有效片段，更换歌曲。")
                        song = None
                        continue
                except FileNotFoundError:
                    logger.error("ffmpeg/ffprobe 未安装。无法执行静音检测，将禁用此功能。")
                    self.vocals_silence_detection = False  # 禁用后续检测
                    break  # 允许游戏继续
                except Exception as e:
                    logger.error(f"对歌曲 '{song.get('title')}' 进行静音检测时失败: {e}，更换歌曲。")
                    song = None
                    continue
            else:
                break

        if not song or not audio_source:
            logger.error(f"在 {MAX_SONG_RETRIES} 次尝试后，未能找到任何有效的歌曲和音频片段来开始游戏。")
            return None

        # --- 后续处理逻辑 ---
        is_bass_boost = preprocessed_mode == 'bass_only'
        has_speed_change = kwargs.get("speed_multiplier", 1.0) != 1.0
        has_reverse = kwargs.get("reverse_audio", False)
        has_band_pass = kwargs.get("band_pass")
        use_slow_path = is_bass_boost or has_speed_change or has_reverse or has_band_pass

        clip_duration = self.config.clip_duration_seconds
        clip_path_obj = self.output_dir / f"clip_{int(time.time())}.mp3"
        mode_key = kwargs.get("random_mode_name") or kwargs.get('play_preprocessed') or (
            "melody_to_piano" if is_piano_mode else "normal")

        # 路径 1: 人声模式的快速路径
        if preprocessed_mode == 'vocals_only' and not use_slow_path and forced_start_ms is not None:
            logger.debug("人声模式无复杂效果，使用ffmpeg快速路径进行裁剪。")
            # [重构] 调用 audio_processor
            success = await self.audio_processor.clip_audio_ffmpeg_fast(audio_source, clip_path_obj, forced_start_ms,
                                                                        clip_duration)
            if success:
                return {"song": song, "clip_path": str(clip_path_obj), "score": kwargs.get("score", 1),
                        "mode": mode_key, "game_type": kwargs.get('game_type')}
            else:
                logger.warning("人声模式快速裁剪失败，将回退到pydub慢速路径。")
                use_slow_path = True  # 强制进入慢速路径

        # 路径 2: 其他简单模式的快速路径
        if not use_slow_path:
            try:
                # [重构] 调用 audio_processor
                total_duration_ms = await loop.run_in_executor(self.executor,
                                                               self.audio_processor.get_duration_ms_ffprobe_sync,
                                                               audio_source)
                if total_duration_ms is None: raise ValueError("ffprobe failed or not found.")

                target_duration_ms = int(clip_duration * 1000)
                if preprocessed_mode in ["drums_only", "bass_only"]: target_duration_ms *= 2
                start_range_min = 0
                if not preprocessed_mode and not is_piano_mode:
                    start_range_min = int(song.get("fillerSec", 0) * 1000)
                start_range_max = int(total_duration_ms - target_duration_ms)
                start_ms = random.randint(start_range_min,
                                          start_range_max) if start_range_min < start_range_max else start_range_min

                # [重构] 调用 audio_processor
                success = await self.audio_processor.clip_audio_ffmpeg_fast(audio_source, clip_path_obj, start_ms,
                                                                            target_duration_ms / 1000.0)
                if not success: raise RuntimeError("ffmpeg clipping failed")

                return {"song": song, "clip_path": str(clip_path_obj), "score": kwargs.get("score", 1),
                        "mode": mode_key, "game_type": kwargs.get('game_type')}
            except Exception as e:
                logger.warning(f"快速路径处理失败: {e}. 将回退到 pydub 慢速路径。")

        # 路径 3: 慢速路径 (pydub)
        try:
            # [重构] 调用 audio_processor
            audio_data = await self.audio_processor.get_audio_data(audio_source)
            if audio_data is None:
                raise RuntimeError("Failed to get audio data (download failed?)")

            pydub_kwargs = {
                "preprocessed_mode": preprocessed_mode,
                "target_duration_seconds": self.config.clip_duration_seconds,
                "speed_multiplier": kwargs.get("speed_multiplier", 1.0),
                "reverse_audio": kwargs.get("reverse_audio", False),
                "band_pass": kwargs.get("band_pass"),
                "is_piano_mode": is_piano_mode,
                "song_filler_sec": song.get("fillerSec", 0),
                "force_start_ms": forced_start_ms
            }

            # [重构] 调用 audio_processor
            clip = await loop.run_in_executor(self.executor, self.audio_processor.process_audio_with_pydub, audio_data,
                                              "mp3", pydub_kwargs)
            if clip is None: raise RuntimeError("pydub audio processing failed.")

            clip.export(clip_path_obj, format="mp3", bitrate="128k")
            return {"song": song, "clip_path": str(clip_path_obj), "score": kwargs.get("score", 1), "mode": mode_key,
                    "game_type": kwargs.get('game_type')}
        except Exception as e:
            logger.error(f"慢速路径 (pydub) 处理音频文件 {audio_source} 时失败: {e}", exc_info=True)
            return None

    def get_random_mode_config(self) -> Tuple[Dict, int, str, str]:
        """生成随机模式的配置。"""
        combinations_by_score = self._precompute_random_combinations()
        if not combinations_by_score: return {}, 0, "", ""

        target_distribution = self._get_random_target_distribution(combinations_by_score)
        scores = list(target_distribution.keys())
        probabilities = list(target_distribution.values())
        target_score = random.choices(scores, weights=probabilities, k=1)[0]

        valid_combinations = combinations_by_score[target_score]
        chosen_processed_combo = random.choice(valid_combinations)

        combined_kwargs = chosen_processed_combo['final_kwargs']
        total_score = chosen_processed_combo['final_score']

        effect_names = [eff['name'] for eff in chosen_processed_combo['effects_list']]
        effect_names_display = sorted(list(set(effect_names)))
        speed_mult = combined_kwargs.get('speed_multiplier')
        has_reverse = 'reverse_audio' in combined_kwargs

        if speed_mult and has_reverse:
            effect_names_display = [n for n in effect_names_display if n not in ['倒放', '2倍速', '1.5倍速']]
            effect_names_display.append(f"倒放+{speed_mult}倍速组合(+1分)")

        mode_name_str = '+'.join(sorted([name.replace(' ver.', '') for name in effect_names if name != 'Off']))
        return combined_kwargs, total_score, "、".join(effect_names_display), mode_name_str

    def _precompute_random_combinations(self) -> Dict[int, List[Dict]]:
        """预计算所有可行的随机效果组合。"""
        combinations_by_score = defaultdict(list)
        playable_source_effects = []
        for effect in self.source_effects:
            kwargs = effect.get('kwargs', {})
            if 'play_preprocessed' in kwargs:
                mode = kwargs['play_preprocessed']
                if self.cache_service.preprocessed_tracks.get(mode):
                    playable_source_effects.append(effect)
            elif 'melody_to_piano' in kwargs:
                if self.cache_service.available_piano_songs:
                    playable_source_effects.append(effect)
            else:
                playable_source_effects.append(effect)

        independent_options = []
        active_base_effects = [] if self.config.lightweight_mode else self.base_effects
        for effect in active_base_effects:
            independent_options.append([effect, {'name': 'Off', 'score': 0, 'kwargs': {}}])

        if not playable_source_effects:
            return {}

        for source_effect in playable_source_effects:
            for independent_choices in itertools.product(*independent_options):
                is_piano_mode = 'melody_to_piano' in source_effect.get('kwargs', {})
                has_reverse_effect = any('reverse_audio' in choice.get('kwargs', {}) for choice in independent_choices)
                if is_piano_mode and has_reverse_effect:
                    continue

                raw_combination = [source_effect] + [choice for choice in independent_choices if choice['score'] > 0]
                final_effects_list = []
                final_kwargs = {}
                base_score = 0
                is_multi_effect = len(raw_combination) > 1

                for effect_template in raw_combination:
                    effect = {k: (v.copy() if isinstance(v, dict) else v) for k, v in effect_template.items()}
                    if is_multi_effect and 'speed_multiplier' in effect.get('kwargs', {}):
                        effect['kwargs']['speed_multiplier'] = 1.5
                        effect['name'] = '1.5倍速'
                    final_effects_list.append(effect)
                    final_kwargs.update(effect.get('kwargs', {}))
                    base_score += effect.get('score', 0)

                final_score = base_score
                if 'speed_multiplier' in final_kwargs and 'reverse_audio' in final_kwargs:
                    final_score += 1

                processed_combo = {
                    'effects_list': final_effects_list,
                    'final_kwargs': final_kwargs,
                    'final_score': final_score,
                }
                combinations_by_score[final_score].append(processed_combo)
        return dict(combinations_by_score)

    def _get_random_target_distribution(self, combinations_by_score: Dict[int, list]) -> Dict[int, float]:
        """根据预计算的组合和衰减因子，生成目标分数概率分布。"""
        if not combinations_by_score: return {}
        scores = sorted(combinations_by_score.keys())
        decay_factor = self.random_mode_decay_factor
        weights = [decay_factor ** score for score in scores]
        total_weight = sum(weights)
        if total_weight == 0:
            return {score: 1.0 / len(scores) for score in scores}
        probabilities = [w / total_weight for w in weights]
        return dict(zip(scores, probabilities))

    def _mode_display_name(self, mode_key: str) -> str:
        """(重构) 题型名美化，支持稳定ID"""
        default_map = {"normal": "普通"}
        if mode_key in default_map: return default_map[mode_key]
        if mode_key.startswith("random_"):
            ids = mode_key.replace("random_", "").split('+')
            names = [self.game_effects.get(i, {}).get('name', i) for i in ids]
            return "随机-" + "+".join(names)
        return self.game_effects.get(mode_key, {}).get('name', mode_key)

    async def get_listen_song_and_path(self, mode: str, search_term: Optional[str]) -> Tuple[
        Optional[Dict], Optional[Union[Path, str]]]:
        """获取听歌模式的歌曲和文件路径。"""
        config = self.listen_modes[mode]
        available_songs = getattr(self.cache_service, config['list_attr'])
        song_to_play = None

        if search_term:
            if search_term.isdigit():
                music_id_to_find = int(search_term)
                song_to_play = next((s for s in available_songs if s['id'] == music_id_to_find), None)
            else:
                found_songs = [s for s in available_songs if search_term.lower() in s['title'].lower()]
                if found_songs:
                    exact_match = next((s for s in found_songs if s['title'].lower() == search_term.lower()), None)
                    song_to_play = exact_match or min(found_songs, key=lambda s: len(s['title']))
        else:
            if not available_songs:
                return None, None
            song_to_play = random.choice(available_songs)

        if not song_to_play:
            return None, None

        mp3_source: Optional[Union[Path, str]] = None
        if config['is_piano']:
            all_song_bundles = {v['vocalAssetbundleName'] for v in song_to_play.get('vocals', [])}
            valid_piano_bundles = list(all_song_bundles.intersection(self.cache_service.available_piano_songs_bundles))
            if valid_piano_bundles:
                chosen_bundle = random.choice(valid_piano_bundles)
                relative_path = f"songs_piano_trimmed_mp3/{chosen_bundle}/{chosen_bundle}.mp3"
                mp3_source = self.cache_service.get_resource_path_or_url(relative_path)
        else:
            sekai_ver = next((v for v in song_to_play.get('vocals', []) if v.get('musicVocalType') == 'sekai'), None)
            bundle_name = None
            if sekai_ver:
                bundle_name = sekai_ver.get('vocalAssetbundleName')
            elif song_to_play.get('vocals'):
                bundle_name = song_to_play['vocals'][0].get('vocalAssetbundleName')

            if bundle_name and bundle_name in self.cache_service.preprocessed_tracks[config['file_key']]:
                relative_path = f"{config['file_key']}/{bundle_name}.mp3"
                mp3_source = self.cache_service.get_resource_path_or_url(relative_path)

        return song_to_play, mp3_source

    async def get_anvo_song_and_vocal(self, content: str) -> Tuple[Optional[Dict], Optional[Union[Dict, str]]]:
        """根据用户输入解析并返回Another Vocal歌曲和版本。"""
        # (此函数不依赖 audio_processor，保持不变)
        song_to_play, vocal_info = None, None

        # 依赖 cache_service 的数据
        another_vocal_songs = self.cache_service.another_vocal_songs
        char_id_to_anov_songs = self.cache_service.char_id_to_anov_songs
        abbr_to_char_id = self.cache_service.abbr_to_char_id

        if not content:
            if not another_vocal_songs: return None, None
            song_to_play = random.choice(another_vocal_songs)
            anov_list = [v for v in song_to_play.get('vocals', []) if v.get('musicVocalType') == 'another_vocal']
            if anov_list: vocal_info = random.choice(anov_list)
        else:
            parts = content.rsplit(maxsplit=1)
            last_part = parts[-1].lower()

            is_char_combo = True
            target_ids = set()
            for abbr in last_part.split('+'):
                char_id = abbr_to_char_id.get(abbr)
                if char_id is None:
                    is_char_combo = False
                    break
                target_ids.add(char_id)

            if is_char_combo and len(parts) > 1:
                song_query = parts[0]
                song_to_play = self.cache_service.find_song_by_query(song_query)
                if song_to_play:
                    for v in song_to_play.get('vocals', []):
                        if v.get('musicVocalType') == 'another_vocal' and {c.get('characterId') for c in
                                                                           v.get('characters', [])} == target_ids:
                            vocal_info = v
                            break
            else:
                if len(parts) == 1 and is_char_combo and len(target_ids) == 1:
                    char_id = list(target_ids)[0]
                    songs_by_char = char_id_to_anov_songs.get(char_id)
                    if songs_by_char:
                        song_to_play = random.choice(songs_by_char)
                        solo = next((v for v in song_to_play.get('vocals', []) if
                                     v.get('musicVocalType') == 'another_vocal' and len(
                                         v.get('characters', [])) == 1 and v['characters'][0].get(
                                         'characterId') == char_id), None)
                        vocal_info = solo or next((v for v in song_to_play.get('vocals', []) if
                                                   v.get('musicVocalType') == 'another_vocal' and any(
                                                       c.get('characterId') == char_id for c in
                                                       v.get('characters', []))), None)
                else:
                    song_to_play = self.cache_service.find_song_by_query(content)
                    if song_to_play:
                        vocal_info = 'list_versions'

        return song_to_play, vocal_info