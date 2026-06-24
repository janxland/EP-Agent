"""
Tool 3: midi_writer
输入: QuantizedScore
输出: .mid 文件（标准 MIDI，可导入 DAW）

依赖: mido（pip install mido）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.note_event import QuantizedScore

try:
    import mido
    HAS_MIDO = True
except ImportError:
    HAS_MIDO = False


def to_midi(score: QuantizedScore, output_path: str,
            instrument: int = 0,
            add_expression: bool = True,
            humanize_ticks: int = 0) -> str:
    """
    QuantizedScore → 标准 MIDI 文件

    Args:
        score:           标准化乐谱
        output_path:     输出 .mid 文件路径
        instrument:      GM 音色编号（0=钢琴, 40=小提琴, 73=长笛）
        add_expression:  自动加力度曲线（强拍稍强）
        humanize_ticks:  微时间偏移量（0=关闭, 建议 4-8）

    Returns:
        output_path（写入成功后返回）
    """
    if not HAS_MIDO:
        raise ImportError("请先安装 mido：pip install mido")

    import random
    ppq  = score.ppq
    bpm  = score.bpm

    mid  = mido.MidiFile(type=0, ticks_per_beat=ppq)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # ── Meta 事件 ──
    tempo_us = int(60_000_000 / bpm)
    track.append(mido.MetaMessage("set_tempo",    tempo=tempo_us, time=0))
    track.append(mido.MetaMessage("time_signature",
                                  numerator=score.time_sig_num,
                                  denominator=score.time_sig_den,
                                  clocks_per_click=24,
                                  notated_32nd_notes_per_beat=8,
                                  time=0))
    # MIDI meta track_name 只支持 latin-1，中文标题转为 ASCII 拼音或直接用空串
    safe_title = score.title.encode("ascii", errors="ignore").decode("ascii") or "Sky Music"
    track.append(mido.MetaMessage("track_name", name=safe_title, time=0))

    # ── 音色设置 ──
    track.append(mido.Message("program_change", channel=0,
                               program=instrument, time=0))

    # ── 构建绝对时间事件列表 ──
    events = []  # (abs_tick, type, pitch, velocity)
    beat_ticks = ppq  # 一拍 = ppq ticks

    for note in score.notes:
        vel = note.velocity

        if add_expression:
            # 强拍（小节第1、3拍）力度+10，弱拍-5
            beat_pos = (note.tick % (beat_ticks * score.time_sig_num)) // beat_ticks
            if beat_pos == 0:
                vel = min(127, vel + 12)
            elif beat_pos == 2:
                vel = min(127, vel + 6)
            else:
                vel = max(40,  vel - 5)

        # 微时间偏移（humanize）
        jitter = random.randint(-humanize_ticks, humanize_ticks) if humanize_ticks else 0

        on_tick  = max(0, note.tick + jitter)
        off_tick = max(on_tick + 1, note.tick + note.duration + jitter)

        events.append((on_tick,  "note_on",  note.pitch, vel))
        events.append((off_tick, "note_off", note.pitch, 0))

    # ── 按绝对 tick 排序 ──
    events.sort(key=lambda e: (e[0], 0 if e[1] == "note_off" else 1))

    # ── 转为 delta-time MIDI 消息 ──
    prev_tick = 0
    for (abs_tick, msg_type, pitch, vel) in events:
        delta = abs_tick - prev_tick
        track.append(mido.Message(msg_type, channel=0,
                                  note=pitch, velocity=vel, time=delta))
        prev_tick = abs_tick

    track.append(mido.MetaMessage("end_of_track", time=0))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    mid.save(output_path)
    return output_path


if __name__ == "__main__":
    from tools.parser import parse_game_score
    path = sys.argv[1] if len(sys.argv) > 1 else None
    out  = sys.argv[2] if len(sys.argv) > 2 else "output.mid"
    if not path:
        print("Usage: python midi_writer.py <sky_json.txt> [output.mid]")
        sys.exit(1)
    score = parse_game_score(path)
    result = to_midi(score, out, add_expression=True, humanize_ticks=6)
    print(f"MIDI saved → {result}")
