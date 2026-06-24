"""
Tool 1: parse_game_score
输入: Sky/CUBY JSON 文本（str 或 文件路径）
输出: QuantizedScore（标准化数据结构）

解析优先级（来源: src/api/apiSongs.ts buildMergedSongsFromSongNotes）:
1. note 自带 pitch 字段 → 直接用作 MIDI 音高
2. key 是和弦名（C/Dm/Em...）→ chordKeyMap 展开
3. key 是 1Key{n}/2Key{n} → keyMap 转 MIDI
4. 同一 tick/time 的音合并为和弦，去重

LEGACY_JSON 模式: pitch + pitchLevel 叠加到所有音高
CUBY_JSON 模式: 键位保持相对，发声由运行时决定（本工具按 LEGACY 处理）
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict
from models.note_event import NoteEvent, QuantizedScore
from mappings.sky_keys import parse_sky_key, apply_pitch_offset, PITCH_OFFSET, FLAT_KEYS

PPQ = 480


def parse_game_score(source: str, quantize_grid: int = 16) -> QuantizedScore:
    """
    解析 Sky/CUBY JSON 谱 → QuantizedScore

    Args:
        source:         JSON 字符串 或 .txt/.json 文件路径
        quantize_grid:  量化精度，16=十六分音符，8=八分音符
    """
    # 1. 读取 JSON
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.loads(source)

    if isinstance(raw, list):
        raw = raw[0]

    # 2. 提取元数据
    title          = raw.get("name", "Untitled")
    composer       = (raw.get("author") or "").strip()
    arranged_by    = (raw.get("arrangedBy") or "").strip()
    transcribed_by = (raw.get("transcribedBy") or "").strip()
    raw_bpm        = float(raw.get("bpm", 240))
    bits_per_page  = int(raw.get("bitsPerPage", 16))
    pitch          = raw.get("pitch", "C")
    pitch_level    = int(raw.get("pitchLevel", 0))
    song_notes     = raw.get("songNotes", [])

    pitch_offset = PITCH_OFFSET.get(pitch, 0) + pitch_level

    # 3. BPM 换算（Sky raw BPM 通常是实际速度的2倍）
    play_bpm    = raw_bpm / 2.0
    beat_ms     = 60000.0 / play_bpm
    ms_per_tick = beat_ms / PPQ
    grid_ticks  = PPQ / (quantize_grid / 4)

    def quantize(t_ms: float) -> int:
        return int(round(t_ms / ms_per_tick / grid_ticks) * grid_ticks)

    # 4. 按 time 分组（同一时刻 = 和弦）
    time_groups = defaultdict(list)
    for n in song_notes:
        time_groups[float(n["time"])].append(n)

    # 5. 构建 NoteEvent 列表
    sorted_times = sorted(time_groups.keys())
    notes = []

    for i, t_ms in enumerate(sorted_times):
        group = time_groups[t_ms]
        qt    = quantize(t_ms)

        # 时长：到下一组的间隔
        if i + 1 < len(sorted_times):
            next_qt = quantize(sorted_times[i + 1])
            dur = max(next_qt - qt, int(grid_ticks))
        else:
            dur = PPQ

        # 收集本组所有 MIDI 音高（去重）
        midi_set = set()
        for n in group:
            # 优先级1: note 自带 pitch（直接 MIDI）
            if "pitch" in n and n["pitch"] is not None:
                midi_set.add(int(n["pitch"]))
                continue
            # 优先级2/3: 解析 key 字段
            key = n.get("key", "")
            for midi in parse_sky_key(key):
                # LEGACY_JSON: 叠加调性偏移
                midi_set.add(apply_pitch_offset(midi, pitch, pitch_level))

        # 按音高排序，分配 channel
        for ch, midi in enumerate(sorted(midi_set)):
            notes.append(NoteEvent(
                tick=qt, pitch=midi, duration=dur,
                velocity=80, channel=min(ch, 15),
            ))

    notes.sort(key=lambda n: (n.tick, n.pitch))

    return QuantizedScore(
        title=title,
        composer=composer,
        arranged_by=arranged_by,
        transcribed_by=transcribed_by,
        bpm=play_bpm,
        time_sig_num=4,
        time_sig_den=4,
        ppq=PPQ,
        key=pitch,
        pitch_offset=pitch_offset,
        pitch_level=pitch_level,
        notes=notes,
        raw_bpm=raw_bpm,
        bits_per_page=bits_per_page,
    )


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: python parser.py <sky_json.txt>"); sys.exit(1)
    score = parse_game_score(path)
    print(f"Title   : {score.title}")
    print(f"BPM     : {score.bpm}  (raw={score.raw_bpm})")
    print(f"Key     : {score.key}  offset={score.pitch_offset}")
    print(f"Notes   : {len(score.notes)}")
    print(f"Duration: {score.duration_ms():.0f} ms")
    print("\n前10个音符:")
    for n in score.notes[:10]:
        print(f"  tick={n.tick:5d}  pitch={n.pitch:3d}  dur={n.duration:4d}  ch={n.channel}")
