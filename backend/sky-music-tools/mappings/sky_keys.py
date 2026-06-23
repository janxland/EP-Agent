"""
Sky / CUBY 键位映射表
来源: src/api/apiSongs.ts, src/constants/index.ts

核心规则:
- 1Key{n} 和 2Key{n} 映射到完全相同的 MIDI 音高
- 1Key* = 第0轨（主旋律），2Key* = 其他轨（伴奏/和声）
- 键号 n 直接对应音阶步进（非半音），见下表
- pitch/pitchLevel 决定实际发声偏移（LEGACY_JSON 模式）
"""

# ── 主映射表（键名后缀 → MIDI 音高，绝对值）──
# 来源: src/api/apiSongs.ts:136, src/constants/index.ts:16
KEY_TO_MIDI = {
    0:  60,  # C4  键盘显示: 1
    1:  62,  # D4  键盘显示: 2
    2:  64,  # E4  键盘显示: 3
    3:  65,  # F4  键盘显示: 4
    4:  67,  # G4  键盘显示: 5
    5:  69,  # A4  键盘显示: 6
    6:  71,  # B4  键盘显示: 7
    7:  72,  # C5  键盘显示: 1·
    8:  74,  # D5  键盘显示: 2·
    9:  76,  # E5  键盘显示: 3·
    10: 77,  # F5  键盘显示: 4·
    11: 79,  # G5  键盘显示: 5·
    12: 81,  # A5  键盘显示: 6·
    13: 83,  # B5  键盘显示: 7·
    14: 84,  # C6  键盘显示: 1··
}

# 和弦预设表（来源: src/api/apiSongs.ts:170）
CHORD_KEY_MAP = {
    "c":  [60, 64, 67],
    "dm": [62, 65, 69],
    "em": [64, 67, 71],
    "f":  [65, 69, 72],
    "g":  [67, 71, 74],
    "am": [69, 72, 76],
    "bm": [71, 74, 78],
}

# pitch 字段 → 半音偏移（来源: src/utils/keySignature.ts）
PITCH_OFFSET = {
    "C":  0,
    "Db": 1,  "C#": 1,
    "D":  2,
    "Eb": 3,  "D#": 3,
    "E":  4,
    "F":  5,
    "Gb": 6,  "F#": 6,
    "G":  7,
    "Ab": 8,  "G#": 8,
    "A":  9,
    "Bb": 10, "A#": 10,
    "B":  11,
}

# 降号调性集合（用于 ABC 音符生成时选择升/降号）
FLAT_KEYS  = {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"}
SHARP_KEYS = {"G", "D", "A", "E", "B", "F#", "C#"}

# ABC 调号映射
ABC_KEY_MAP = {
    "C": "C", "Db": "Db", "C#": "C#", "D": "D",
    "Eb": "Eb", "D#": "D#", "E": "E", "F": "F",
    "Gb": "Gb", "F#": "F#", "G": "G", "Ab": "Ab",
    "G#": "G#", "A": "A", "Bb": "Bb", "A#": "A#", "B": "B",
}


def parse_sky_key(sky_key: str) -> list:
    """
    解析 Sky 键名 → MIDI 音高列表

    支持:
    - "1Key{n}" / "2Key{n}" → 单音 [MIDI]
    - 和弦名 "C" / "Dm" 等 → 多音 [MIDI, ...]
    - 带 pitch 字段的直接音高（由 parser 处理）

    Returns:
        list of MIDI pitch values
    """
    key_lower = sky_key.lower().strip()

    # 和弦名（如 "C", "Dm", "Am"）
    if key_lower in CHORD_KEY_MAP:
        return CHORD_KEY_MAP[key_lower]

    # 1Key{n} 或 2Key{n}
    for prefix in ("1key", "2key"):
        if key_lower.startswith(prefix):
            try:
                n = int(sky_key[len(prefix):])
                midi = KEY_TO_MIDI.get(n)
                if midi is not None:
                    return [midi]
            except ValueError:
                pass

    # fallback: 尝试直接解析数字
    try:
        n = int("".join(filter(str.isdigit, sky_key)))
        return [KEY_TO_MIDI.get(n % 15, 60)]
    except:
        return [60]


def apply_pitch_offset(midi: int, pitch: str, pitch_level: int = 0) -> int:
    """
    LEGACY_JSON 模式：给 MIDI 音高叠加调性偏移
    CUBY_JSON 模式：不调用此函数（键位保持相对，发声由运行时决定）
    """
    offset = PITCH_OFFSET.get(pitch, 0) + pitch_level
    return midi + offset


def midi_to_abc(midi: int, use_flats: bool = True) -> str:
    """MIDI 音高 → ABC 音符名（含八度标记）"""
    if use_flats:
        note_names = ["C", "_D", "D", "_E", "E", "F", "_G", "G", "_A", "A", "_B", "B"]
    else:
        note_names = ["C", "^C", "D", "^D", "E", "F", "^F", "G", "^G", "A", "^A", "B"]

    octave = midi // 12 - 1
    note   = note_names[midi % 12]
    base   = note[-1].upper()
    prefix = note[:-1] if len(note) > 1 else ""

    if octave <= 3:
        return prefix + base + "," * (4 - octave)
    elif octave == 4:
        return prefix + base
    elif octave == 5:
        return prefix + base.lower()
    elif octave == 6:
        return prefix + base.lower() + "'"
    else:
        return prefix + base.lower() + "'" * (octave - 6)
