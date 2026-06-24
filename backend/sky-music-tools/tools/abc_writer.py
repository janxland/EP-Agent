"""
Tool 2: abc_writer
输入: QuantizedScore
输出: ABC Notation 字符串
支持: 和弦（同 tick 多音符 → [chord]）、半音键
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.note_event import QuantizedScore
from mappings.sky_keys import midi_to_abc, FLAT_KEYS, ABC_KEY_MAP
from collections import defaultdict


def to_abc_notation(score: QuantizedScore) -> str:
    """QuantizedScore → ABC Notation 字符串"""
    use_flats = score.key in FLAT_KEYS
    abc_key   = ABC_KEY_MAP.get(score.key, score.key)
    ppq       = score.ppq
    unit_ticks = ppq // 2   # L=1/8，八分音符单位
    BAR_UNITS  = 8           # 4/4 每小节8个单位

    # ── 按 tick 分组，合并和弦 ──
    tick_groups = defaultdict(list)
    for note in score.notes:
        tick_groups[note.tick].append(note)

    sorted_ticks = sorted(tick_groups.keys())

    # ── 构建事件流（note/rest, abc_str, units）──
    events = []
    prev_end = 0

    for tick in sorted_ticks:
        group = tick_groups[tick]

        # 填充休止符
        gap = tick - prev_end
        if gap > 0:
            gap_u = max(1, round(gap / unit_ticks))
            events.append(("rest", "z", gap_u))

        # 和弦时长取组内最长
        dur = max(n.duration for n in group)
        note_u = max(1, round(dur / unit_ticks))

        if len(group) == 1:
            # 单音
            abc_note = midi_to_abc(group[0].pitch, use_flats)
            events.append(("note", abc_note, note_u))
        else:
            # 和弦：按音高排序，低→高，用 [abc1abc2...] 格式
            pitches = sorted(set(n.pitch for n in group))
            chord_str = "[" + "".join(midi_to_abc(p, use_flats) for p in pitches) + "]"
            events.append(("note", chord_str, note_u))

        prev_end = tick + dur

    # ── 合法时值分解：将任意单位数拆成 ABC 标准允许的时值 ──
    # ABC 标准只允许 1,2,3,4,6,8,12,16 等（2的幂或附点），不允许 5,7,9,10,11...
    # 拆分规则：贪心取最大合法值，剩余用连音线连接
    VALID_UNITS = [8, 6, 4, 3, 2, 1]  # 降序合法时值

    def split_units(u):
        """将 u 拆成合法时值列表，如 5→[4,1], 7→[4,3], 10→[8,2]"""
        result = []
        remaining = u
        for v in VALID_UNITS:
            while remaining >= v:
                result.append(v)
                remaining -= v
        return result if result else [1]

    def units_to_str(u):
        return "" if u == 1 else str(u)

    def render_sym(sym, units, is_rest):
        """将 sym 渲染为带时值的 ABC 片段，不可表达的时值用连音线拆分"""
        parts = split_units(units)
        if len(parts) == 1:
            return f"{sym}{units_to_str(parts[0])}"
        # 多段：休止符直接拼接，音符用连音线
        if is_rest:
            return "".join(f"z{units_to_str(p)}" for p in parts)
        else:
            return "-".join(f"{sym}{units_to_str(p)}" for p in parts)

    bars, current_bar, bar_fill = [], [], 0
    for (etype, sym, units) in events:
        is_rest = (etype == "rest")
        remaining = units
        while remaining > 0:
            space = BAR_UNITS - bar_fill
            take  = min(remaining, space)
            if take > 0:
                current_bar.append(render_sym(sym, take, is_rest))
                bar_fill += take
                remaining -= take
            if bar_fill >= BAR_UNITS:
                bars.append(current_bar)
                current_bar, bar_fill = [], 0

    if current_bar:
        space = BAR_UNITS - bar_fill
        if space > 0:
            current_bar.append(render_sym("z", space, is_rest=True))
        bars.append(current_bar)

    # ── 每行4小节 ──
    body_lines = []
    for i in range(0, len(bars), 4):
        chunk = bars[i:i+4]
        body_lines.append(" | ".join("".join(b) for b in chunk) + " |")

    # ── 专业 ABC Header（完整映射所有 JSON 字段）──
    # 参考 ABC Notation 标准: https://abcnotation.com/wiki/abc:standard:v2.1
    lines = ["X:1"]
    lines.append(f"T:{score.title}")                          # T: 曲名 ← name

    if score.composer:
        lines.append(f"C:{score.composer}")                   # C: 作曲 ← author
    if score.arranged_by:
        lines.append(f"A:{score.arranged_by}")                # A: 编曲 ← arrangedBy
    if score.transcribed_by:
        lines.append(f"Z:{score.transcribed_by}")             # Z: 记谱 ← transcribedBy

    # S: 来源信息（包含原始游戏BPM和pitchLevel供溯源）
    source_parts = [f"raw_bpm={int(score.raw_bpm)}"]
    if score.pitch_level:
        source_parts.append(f"pitchLevel={score.pitch_level}")
    lines.append(f"S:Sky/CUBY JSON  {' '.join(source_parts)}")

    lines.append(f"M:{score.time_sig_num}/{score.time_sig_den}")  # M: 拍号
    lines.append(f"L:1/8")                                        # L: 基本音符长度
    lines.append(f"Q:1/4={int(round(score.bpm))}")                # Q: 速度 ← bpm/2
    lines.append(f"K:{abc_key}")                                   # K: 调号 ← pitch+pitchLevel

    header = "\n".join(lines) + "\n"
    return header + "\n".join(body_lines)
