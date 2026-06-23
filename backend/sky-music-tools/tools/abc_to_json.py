"""
Tool 5: abc_to_json  (反向转换)
输入: ABC Notation 字符串
输出: CUBY/Sky 标准 JSON 格式（songNotes 数组）

核心逻辑:
1. 解析 ABC Header 提取元数据（T/C/A/Z/Q/K/M/L）
2. 解析 ABC 正文提取每个音符/和弦的音高和时值
3. MIDI 音高 → 反查 KEY_TO_MIDI → 生成 1Key{n} / 2Key{n}
4. tick → 毫秒时间戳（基于 BPM）
5. 和弦拆分：同一时刻多音 → 多条 songNote 相同 time
"""
import re
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mappings.sky_keys import KEY_TO_MIDI, PITCH_OFFSET

# ── 反向映射：MIDI → Key 编号 ──
MIDI_TO_KEY = {v: k for k, v in KEY_TO_MIDI.items()}

# ABC 音符名 → 半音偏移（相对C4=60）
ABC_NOTE_SEMITONE = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11
}


def abc_note_to_midi(note_str: str, key_sig_offset: int = 0) -> int:
    """
    ABC 单音符字符串 → MIDI 音高
    支持: C D E F G A B c d e f g a b c' d' ...
          _C ^C _d ^d 等升降号
          C, C,, 等低八度标记
    """
    s = note_str.strip()
    if not s:
        return -1

    # 解析前缀升降号
    accidental = 0
    i = 0
    while i < len(s) and s[i] in '_^=':
        if s[i] == '_':
            accidental -= 1
        elif s[i] == '^':
            accidental += 1
        i += 1

    if i >= len(s):
        return -1

    # 音名
    note_char = s[i]
    if note_char not in 'CDEFGABcdefgab':
        return -1
    i += 1

    # 八度：大写=4，小写=5，'=+1，,=-1
    base_octave = 4 if note_char.isupper() else 5
    note_upper = note_char.upper()

    # 后缀八度修饰
    while i < len(s):
        if s[i] == "'":
            base_octave += 1
            i += 1
        elif s[i] == ',':
            base_octave -= 1
            i += 1
        else:
            break

    semitone = ABC_NOTE_SEMITONE.get(note_upper, 0)
    midi = (base_octave + 1) * 12 + semitone + accidental
    return midi


def parse_abc_header(abc_str: str) -> dict:
    """解析 ABC Header → 元数据字典"""
    meta = {
        'title': 'Untitled',
        'composer': '',
        'arranged_by': '',
        'transcribed_by': '',
        'bpm': 120,
        'raw_bpm': 240,
        'key': 'C',
        'pitch_level': 0,
        'time_sig': (4, 4),
        'unit': (1, 8),   # L: 基本音符长度
    }
    for line in abc_str.splitlines():
        line = line.strip()
        if line.startswith('T:'):
            meta['title'] = line[2:].strip()
        elif line.startswith('C:'):
            meta['composer'] = line[2:].strip()
        elif line.startswith('A:'):
            meta['arranged_by'] = line[2:].strip()
        elif line.startswith('Z:'):
            meta['transcribed_by'] = line[2:].strip()
        elif line.startswith('Q:'):
            # Q:1/4=120 or Q:120
            m = re.search(r'=(\d+)', line)
            if m:
                meta['bpm'] = int(m.group(1))
                meta['raw_bpm'] = meta['bpm'] * 2
        elif line.startswith('K:'):
            key_str = line[2:].strip()
            meta['key'] = key_str if key_str else 'C'
        elif line.startswith('M:'):
            m = re.match(r'M:(\d+)/(\d+)', line)
            if m:
                meta['time_sig'] = (int(m.group(1)), int(m.group(2)))
        elif line.startswith('L:'):
            m = re.match(r'L:(\d+)/(\d+)', line)
            if m:
                meta['unit'] = (int(m.group(1)), int(m.group(2)))
        elif line.startswith('S:'):
            # S:Sky/CUBY JSON  raw_bpm=321 pitchLevel=1
            m = re.search(r'raw_bpm=(\d+)', line)
            if m:
                meta['raw_bpm'] = int(m.group(1))
            m = re.search(r'pitchLevel=(\d+)', line)
            if m:
                meta['pitch_level'] = int(m.group(1))
    return meta


def tokenize_abc_body(abc_str: str) -> list:
    """
    将 ABC 正文分解为 token 列表
    token 类型: note, chord, rest, barline, tie
    返回: list of (type, value, duration_units)
    """
    # 提取正文（跳过 header 行）
    body_lines = []
    for line in abc_str.splitlines():
        stripped = line.strip()
        if stripped and not re.match(r'^[A-Za-z]:',stripped) and not stripped.startswith('%'):
            body_lines.append(stripped)
    body = ' '.join(body_lines)

    tokens = []
    i = 0
    n = len(body)

    while i < n:
        c = body[i]

        # 跳过空白、小节线、反复记号
        if c in ' \t\n|:[]{}()\\':
            # 处理和弦 [...]
            if c == '[':
                # 找到匹配的 ]
                j = body.find(']', i)
                if j == -1:
                    i += 1
                    continue
                chord_content = body[i+1:j]
                i = j + 1
                # 读取时值数字
                dur_str = ''
                while i < n and (body[i].isdigit() or body[i] == '/'):
                    dur_str += body[i]
                    i += 1
                dur = parse_duration(dur_str)
                # 解析和弦内各音符
                notes = parse_chord_notes(chord_content)
                if notes:
                    tokens.append(('chord', notes, dur))
            else:
                i += 1
            continue

        # 连音线
        if c == '-':
            tokens.append(('tie', None, 0))
            i += 1
            continue

        # 休止符
        if c == 'z' or c == 'x':
            i += 1
            dur_str = ''
            while i < n and (body[i].isdigit() or body[i] == '/'):
                dur_str += body[i]
                i += 1
            dur = parse_duration(dur_str)
            tokens.append(('rest', None, dur))
            continue

        # 升降号前缀
        accidental = ''
        while i < n and body[i] in '_^=':
            accidental += body[i]
            i += 1

        # 音符
        if i < n and body[i] in 'CDEFGABcdefgab':
            note_char = body[i]
            i += 1
            # 八度修饰
            octave_mod = ''
            while i < n and body[i] in "'," :
                octave_mod += body[i]
                i += 1
            # 时值
            dur_str = ''
            while i < n and (body[i].isdigit() or body[i] == '/'):
                dur_str += body[i]
                i += 1
            dur = parse_duration(dur_str)
            note_str = accidental + note_char + octave_mod
            midi = abc_note_to_midi(note_str)
            if midi > 0:
                tokens.append(('note', [midi], dur))
            continue

        i += 1

    return tokens


def parse_chord_notes(chord_str: str) -> list:
    """解析和弦内容字符串，返回 MIDI 列表"""
    midis = []
    i = 0
    n = len(chord_str)
    while i < n:
        # 升降号
        accidental = ''
        while i < n and chord_str[i] in '_^=':
            accidental += chord_str[i]
            i += 1
        if i >= n:
            break
        note_char = chord_str[i]
        if note_char not in 'CDEFGABcdefgab':
            i += 1
            continue
        i += 1
        octave_mod = ''
        while i < n and chord_str[i] in "'," :
            octave_mod += chord_str[i]
            i += 1
        # 跳过和弦内时值（和弦时值统一用外部的）
        while i < n and (chord_str[i].isdigit() or chord_str[i] == '/'):
            i += 1
        note_str = accidental + note_char + octave_mod
        midi = abc_note_to_midi(note_str)
        if midi > 0:
            midis.append(midi)
    return midis


def parse_duration(dur_str: str) -> int:
    """
    ABC 时值字符串 → 单位数（以 L:1/8 为基准）
    '' → 1, '2' → 2, '4' → 4, '/2' → 0 (半个单位，忽略)
    """
    if not dur_str:
        return 1
    if dur_str.startswith('/'):
        # 分数，如 /2 = 0.5 单位，取整
        try:
            return max(1, round(1 / int(dur_str[1:])))
        except:
            return 1
    if '/' in dur_str:
        parts = dur_str.split('/')
        try:
            return max(1, round(int(parts[0]) / int(parts[1])))
        except:
            return 1
    try:
        return int(dur_str)
    except:
        return 1


def midi_to_sky_key(midi: int, pitch_offset: int = 0, track: int = 0) -> str:
    """
    MIDI 音高 → Sky/CUBY 键名
    先撤销 pitch_offset，再反查 KEY_TO_MIDI
    track=0 → 1Key{n}，track>0 → 2Key{n}
    """
    raw_midi = midi - pitch_offset
    # 精确匹配
    if raw_midi in MIDI_TO_KEY:
        n = MIDI_TO_KEY[raw_midi]
        prefix = "1Key" if track == 0 else "2Key"
        return f"{prefix}{n}"
    # 最近邻匹配（处理调性偏移导致的轻微偏差）
    best_n = min(MIDI_TO_KEY.keys(), key=lambda m: abs(m - raw_midi))
    n = MIDI_TO_KEY[best_n]
    prefix = "1Key" if track == 0 else "2Key"
    return f"{prefix}{n}"


def abc_to_cuby_json(abc_str: str) -> dict:
    """
    ABC Notation → CUBY JSON 格式

    Returns:
        dict: 完整 CUBY JSON 对象（可直接 json.dumps）
    """
    meta = parse_abc_header(abc_str)
    tokens = tokenize_abc_body(abc_str)

    bpm       = meta['bpm']
    raw_bpm   = meta['raw_bpm']
    key       = meta['key']
    pitch_lvl = meta['pitch_level']
    pitch_off = PITCH_OFFSET.get(key, 0) + pitch_lvl

    # L:1/8 → 一个单位 = 八分音符
    # 一个八分音符时长(ms) = 60000 / bpm / 2
    unit_ms = 60000.0 / bpm / 2.0

    song_notes = []
    current_time_ms = 0.0
    tie_pending = False   # 连音线标志

    for token in tokens:
        ttype, value, dur = token

        if ttype == 'tie':
            tie_pending = True
            continue

        if ttype == 'rest':
            if not tie_pending:
                current_time_ms += dur * unit_ms
            tie_pending = False
            continue

        if ttype in ('note', 'chord'):
            midis = value  # list of MIDI
            time_ms = int(round(current_time_ms))

            if not tie_pending:
                # 和弦：第一个音用 1Key（主旋律），其余用 2Key（伴奏）
                for track_idx, midi in enumerate(sorted(set(midis))):
                    key_name = midi_to_sky_key(midi, pitch_off, track_idx)
                    song_notes.append({
                        "key": key_name,
                        "time": time_ms
                    })

            # 推进时间
            if not tie_pending:
                current_time_ms += dur * unit_ms
            else:
                # 连音线：延长上一个音，不新增音符，只推进时间
                current_time_ms += dur * unit_ms
                tie_pending = False

    # 构建完整 CUBY JSON
    result = {
        "name": meta['title'],
        "author": meta['composer'],
        "arrangedBy": meta['arranged_by'],
        "transcribedBy": meta['transcribed_by'],
        "bpm": raw_bpm,
        "bitsPerPage": 16,
        "pitch": key if key in PITCH_OFFSET else "C",
        "pitchLevel": pitch_lvl,
        "songNotes": song_notes,
        "extension": None,
        "sort": 0,
        "userId": None,
        "lastAccountId": None
    }
    return result


if __name__ == "__main__":
    abc_input = sys.argv[1] if len(sys.argv) > 1 else None
    if abc_input and os.path.isfile(abc_input):
        with open(abc_input, encoding='utf-8') as f:
            abc_str = f.read()
    elif abc_input:
        abc_str = abc_input
    else:
        print("Usage: python abc_to_json.py <abc_file_or_string>")
        sys.exit(1)

    result = abc_to_cuby_json(abc_str)
    print(json.dumps([result], ensure_ascii=False, indent=2))
