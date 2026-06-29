"""
ABC 编辑工具集 - Agent 可直接调用的原子操作

设计原则：
- 每个工具是一个纯函数，输入/输出都是 str/int/float/dict
- 今天用通用 LLM 实现，未来可无缝替换为垂直模型
- 工具内部的 LLM 调用完全隔离，Agent 编排层不感知

未来扩展点（标注 TODO: VERTICAL_MODEL）：
  可将对应函数的实现替换为专业音乐模型的 API 调用
"""
from __future__ import annotations
import re
import sys
from pathlib import Path
from app.agentcore.tools import tool

# sky-music-tools 已内置在 backend/sky-music-tools/，直接注入路径
_SKY_TOOLS_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "sky-music-tools")
if _SKY_TOOLS_DIR not in sys.path:
    sys.path.insert(0, _SKY_TOOLS_DIR)


# ─── 确定性工具（纯算法，不需要 LLM）────────────────────────

# ABC 音符到半音数的映射（C大调音阶，相对于C4）
_NOTE_TO_SEMITONE = {
    'C': 0,  'D': 2,  'E': 4,  'F': 5,  'G': 7,  'A': 9,  'B': 11,   # 第4八度
    'c': 12, 'd': 14, 'e': 16, 'f': 17, 'g': 19, 'a': 21, 'b': 23,   # 第5八度
}

_SEMITONE_TO_NOTE_SHARP = {
    0: 'C', 1: '^C', 2: 'D', 3: '^D', 4: 'E', 5: 'F',
    6: '^F', 7: 'G', 8: '^G', 9: 'A', 10: '^A', 11: 'B',
}
_SEMITONE_TO_NOTE_FLAT = {
    0: 'C', 1: '_D', 2: 'D', 3: '_E', 4: 'E', 5: 'F',
    6: '_G', 7: 'G', 8: '_A', 9: 'A', 10: '_B', 11: 'B',
}

# 调号名 → 相对 C 的半音偏移
_KEY_SEMITONES = {
    'C': 0, 'G': 7, 'D': 2, 'A': 9, 'E': 4, 'B': 11,
    'F#': 6, 'C#': 1,
    'F': 5, 'Bb': 10, 'Eb': 3, 'Ab': 8, 'Db': 1, 'Gb': 6,
    'Am': 0, 'Em': 7, 'Bm': 2, 'F#m': 9, 'C#m': 4,
    'Dm': 5, 'Gm': 10, 'Cm': 3,
}

# 降号调（转调时优先用降号表示）
_FLAT_KEYS = {'F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Dm', 'Gm', 'Cm'}

_CHROMATIC_KEYS = ['C', '^C', 'D', '^D', 'E', 'F', '^F', 'G', '^G', 'A', '^A', 'B']

# Sky 合法 MIDI 范围：C4(60) ~ C6(84)
_SKY_MIDI_MIN = 60  # C4
_SKY_MIDI_MAX = 84  # C6


@tool
def transpose_abc(abc: str, semitones: int) -> str:
    """将 ABC 谱精确转调指定半音数（纯算法，不调用 LLM）。
    abc: 完整的 ABC Notation 字符串
    semitones: 转调半音数，正数升调，负数降调（范围 -12 到 12）。
      常用对照（从C大调出发）：G=+7, F=+5, D=+2, A=+9, E=+4, B=+11,
      F=+5, Bb=+10, Eb=+3, Ab=+8；降调用负数，如降到F=-7。
      从其他调出发时，先用 analyze_abc 获取当前调号，再计算差值。
    转调后超出 Sky C4-C6 范围的音符会自动移八度（不截断）。
    """
    if semitones == 0:
        return abc

    lines = abc.split('\n')
    result_lines = []
    in_body = False
    use_flats = False

    # 先扫一遍确定目标调是否用降号
    for line in lines:
        if line.startswith('K:'):
            old_key = line[2:].strip().split()[0] if line[2:].strip() else 'C'
            new_key = _shift_key(old_key, semitones)
            use_flats = new_key in _FLAT_KEYS
            break

    for line in lines:
        if line.startswith('K:'):
            old_key = line[2:].strip().split()[0] if line[2:].strip() else 'C'
            new_key = _shift_key(old_key, semitones)
            result_lines.append(f'K:{new_key}')
            in_body = True
            continue

        if not in_body or line.startswith('%') or not line.strip():
            result_lines.append(line)
            continue

        # Header 行（在 K: 之前）保持不变
        if re.match(r'^[A-Za-z]:', line):
            result_lines.append(line)
            continue

        # 转调音符（含越界移八度保护）
        result_lines.append(_transpose_note_line(line, semitones, use_flats))

    return '\n'.join(result_lines)


def _shift_key(key: str, semitones: int) -> str:
    """将调号名按半音偏移，自动选择升/降号表示"""
    is_minor = key.endswith('m') and len(key) > 1
    semitone_val = _KEY_SEMITONES.get(key, 0)
    new_val = (semitone_val + semitones) % 12
    # 优先找精确匹配
    for k, v in _KEY_SEMITONES.items():
        if v == new_val and k.endswith('m') == is_minor:
            return k
    return _CHROMATIC_KEYS[new_val] + ('m' if is_minor else '')


def _note_to_midi(note_char: str, prefix: str) -> int:
    """将 ABC 音符字符+前缀转为相对 C4 的半音值（0=C4, 12=C5, 24=C6）"""
    base = _NOTE_TO_SEMITONE.get(note_char, 0)
    if prefix == '^':
        base += 1
    elif prefix == '_':
        base -= 1
    return base


def _midi_to_abc_note(semitone: int, use_flats: bool) -> str:
    """将相对 C4 的半音值转回 ABC 音符字符串（含八度标记）"""
    # 强制限制在 Sky C4-C6 范围内（0~24），越界移八度
    while semitone < 0:
        semitone += 12
    while semitone > 24:
        semitone -= 12

    octave = semitone // 12   # 0=第4八度(大写), 1=第5八度(小写), 2=C6(小写+')
    base   = semitone % 12

    table = _SEMITONE_TO_NOTE_FLAT if use_flats else _SEMITONE_TO_NOTE_SHARP
    note_str = table.get(base, 'C')

    # 处理升降号前缀
    if note_str.startswith('^') or note_str.startswith('_'):
        acc = note_str[0]
        note_char = note_str[1]
    else:
        acc = ''
        note_char = note_str

    if octave == 0:
        return acc + note_char.upper()
    elif octave == 1:
        return acc + note_char.lower()
    else:  # octave == 2，只有 C6
        return acc + note_char.lower() + "'"


def _transpose_note_line(line: str, semitones: int, use_flats: bool = False) -> str:
    """转调一行 ABC 音符，越界音符自动移八度保持在 Sky C4-C6 范围内"""
    result = []
    i = 0
    while i < len(line):
        c = line[i]

        # 前缀修饰符（升降还原号）
        prefix = ''
        if c in ('^', '_', '='):
            prefix = c
            i += 1
            if i >= len(line):
                result.append(prefix)
                break
            c = line[i]

        if c in _NOTE_TO_SEMITONE:
            # 当前音符的半音值（相对 C4）
            curr_semitone = _note_to_midi(c, prefix if prefix != '=' else '')

            # 检查下一个字符是否是高八度标记 '
            i += 1
            extra_octave = 0
            while i < len(line) and line[i] == "'":
                extra_octave += 1
                i += 1
            curr_semitone += extra_octave * 12

            # 转调
            new_semitone = curr_semitone + semitones

            # 越界移八度（保持在 0~24 范围内）
            while new_semitone < 0:
                new_semitone += 12
            while new_semitone > 24:
                new_semitone -= 12

            result.append(_midi_to_abc_note(new_semitone, use_flats))
        else:
            result.append(prefix + c)
            i += 1

    return ''.join(result)


@tool
def change_tempo(abc: str, bpm: int) -> str:
    """修改 ABC 谱的速度（BPM），精确替换 Q: 字段。
    abc: 完整的 ABC Notation 字符串
    bpm: 目标每分钟节拍数（建议范围 40-240）
    """
    lines = abc.split('\n')
    result = []
    q_found = False
    for line in lines:
        if line.startswith('Q:'):
            result.append(f'Q:1/4={bpm}')
            q_found = True
        else:
            result.append(line)
    if not q_found:
        final = []
        for line in result:
            if line.startswith('K:'):
                final.append(f'Q:1/4={bpm}')
            final.append(line)
        return '\n'.join(final)
    return '\n'.join(result)


@tool
def analyze_abc(abc: str) -> dict:
    """分析 ABC 谱的结构信息，返回调号、速度、拍号、音符数、音域、小节数等。
    abc: 完整的 ABC Notation 字符串
    """
    info: dict = {
        "key": "C", "bpm": 120, "time_sig": "4/4",
        "note_count": 0, "bar_count": 0, "line_count": 0,
        "range_low": None, "range_high": None,
        "has_rests": False, "rhythm_variety": False,
    }
    in_body = False
    time_values_seen = set()

    for line in abc.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('K:'):
            info["key"] = stripped[2:].strip()
            in_body = True
            continue
        if stripped.startswith('Q:'):
            m = re.search(r'=(\d+)', stripped)
            if m:
                info["bpm"] = int(m.group(1))
            continue
        if stripped.startswith('M:'):
            info["time_sig"] = stripped[2:].strip()
            continue
        # 只统计 Body 部分（K: 之后）
        if not in_body:
            continue
        if re.match(r'^[A-Za-z]:', stripped):
            continue

        # 统计音符（只在 Body 中）
        notes_in_line = re.findall(r'[A-Ga-g]', stripped)
        info["note_count"] += len(notes_in_line)
        info["bar_count"]  += stripped.count('|')
        if notes_in_line:
            info["line_count"] += 1

        # 检测休止符
        if 'z' in stripped:
            info["has_rests"] = True

        # 检测节奏多样性（时值种类）
        time_vals = re.findall(r'[A-Ga-gz](\d+(?:/\d+)?)', stripped)
        time_values_seen.update(time_vals)

    # 节奏多样性：时值种类 >= 2 视为有多样性
    info["rhythm_variety"] = len(time_values_seen) >= 2
    info["time_value_types"] = len(time_values_seen)

    return info


# ─── LLM 驱动工具（今天用通用模型，未来可换垂直模型）────────
# TODO: VERTICAL_MODEL - 以下工具可替换为专业音乐模型

# 风格转换的专业 system prompt
_STYLE_SYSTEM = """你是世界顶级的 ABC Notation 音乐编辑专家，精通 Sky: Children of the Light 游戏的 15 键乐器。

Sky 乐器限制：
- 可用音符：C D E F G A B c d e f g a b c'（C4-C6，共15键）
- 超出范围的音符必须移八度处理
- 单声部旋律为主

输出规则（铁律）：
1. 直接输出完整修改后的 ABC 谱（Header + Body）
2. 最后一行写：SUMMARY: 一句话说明做了什么改动
3. 绝对禁止输出 JSON、代码块标记（```）、解释性文字
4. 每行节奏必须多样（禁止全行纯八分音符）"""


@tool
async def change_style(abc: str, style: str) -> str:
    """将 ABC 谱转换为指定音乐风格（爵士/古典/中国风/流行等）。
    使用 LLM 进行风格转换，保持 Sky 15 键范围约束。
    abc: 完整的 ABC Notation 字符串
    style: 目标风格，如 jazz/classical/chinese/pop/folk/electronic
    """
    from app.agentcore.llm import complete
    from app.agentcore.abc_utils import extract_abc_and_summary

    style_guides = {
        "jazz":      "爵士风格：切分节奏（syncopation）、蓝调音（降3/7音）、摇摆感。节奏型：C/2 D/2 E2 | _B/2 A/2 G2",
        "classical": "古典风格：规整对称乐句、级进旋律线条、清晰的强弱对比。节奏型：C2 D2 E2 F2 | G4 E4",
        "chinese":   "中国风格：五声音阶（去掉F和B，只用C D E G A）、附点节奏、长音留白。节奏型：c3/2 d/2 E2 A2 | G6 z2",
        "pop":       "流行风格：神进行和弦感（C-G-Am-F）、切分节奏、朗朗上口的旋律。节奏型：c2 BA G3/2 A/2 | G4 z4",
        "folk":      "民谣风格：自然音阶、波浪式旋律线条、温暖附点律动。节奏型：c3/2 d/2 e3/2 d/2 | c4 B2 A2",
        "electronic":"电子风格：强拍重音、机械节奏感、八度跳进。节奏型：C2 c2 G2 g2 | E4 z4",
    }
    style_key = style.lower().replace("风", "").replace("格", "").strip()
    guide = style_guides.get(style_key, f"{style}风格：保持旋律骨干，转换节奏型和音符装饰")

    prompt = f"""请将以下 ABC 谱转换为{style}风格。

风格指南：{guide}

转换要求：
1. 保留原曲的核心旋律骨干音（强拍音）
2. 根据风格特征改变节奏型和装饰音
3. 每行混用至少2种时值（禁止全行纯八分音符）
4. 所有音符保持在 Sky C4-C6 范围（C D E F G A B c d e f g a b c'）
5. 保持原曲的调号和BPM（除非风格明确要求改变）

当前 ABC 谱：
{abc}"""

    raw = await complete(
        [{"role": "system", "content": _STYLE_SYSTEM},
         {"role": "user",   "content": prompt}],
        temperature=0.78,
    )
    new_abc, _ = extract_abc_and_summary(raw, abc)
    return new_abc


@tool
def validate_abc(abc: str) -> dict:
    """验证 ABC 谱是否符合 Sky 游戏 15 键范围（C4-C6），返回验证结果和超范围音符列表。
    abc: 完整的 ABC Notation 字符串
    """
    out_of_range: list[str] = []
    warnings: list[str] = []
    in_body = False

    for line_no, line in enumerate(abc.split('\n'), 1):
        stripped = line.strip()
        if stripped.startswith('K:'):
            in_body = True
            continue
        if not in_body or not stripped or re.match(r'^[A-Za-z]:', stripped):
            continue

        # 检查高八度标记（c'' 及以上超出范围）
        if re.search(r"[a-gA-G]''", stripped):
            out_of_range.append(f"第{line_no}行：存在超出 C6 的音符（双撇号 ''）")

        # 检查低八度标记（C, 超出范围）
        if re.search(r'[A-G],', stripped):
            out_of_range.append(f"第{line_no}行：存在低于 C4 的音符（逗号降八度 ,）")

        # 检查 c' 之外的高音加撇（d'/e'/f'/g'/a'/b' 均超出Sky范围，只有 c' 合法）
        # 正则：匹配小写音符(非c) + 撇号，前面可能有升降号前缀
        high_notes = re.findall(r'[\^_=]?([a-bd-g])\x27', stripped)
        for note_char in high_notes:
            out_of_range.append(f"第{line_no}行：音符 {note_char}' 超出 Sky C6 上限（只有 c' 合法）")

        # 警告：节奏单调（全行纯八分音符）
        body_content = re.sub(r'[|:\[\]]', '', stripped)
        notes_with_duration = re.findall(r'[A-Ga-gz]\d*', body_content)
        if notes_with_duration and len(notes_with_duration) >= 4:
            no_duration = [n for n in notes_with_duration if not re.search(r'\d', n)]
            if len(no_duration) == len(notes_with_duration):
                warnings.append(f"第{line_no}行：节奏单调（全行纯八分音符），建议混用时值")

    is_valid = len(out_of_range) == 0
    return {
        "valid":        is_valid,
        "out_of_range": out_of_range,
        "warnings":     warnings,
        "message": (
            "ABC 谱符合 Sky 15 键范围" if is_valid
            else f"发现 {len(out_of_range)} 处超范围音符，需要移八度处理"
        ),
    }


@tool
async def add_ornament(abc: str, ornament_type: str) -> str:
    """为 ABC 谱添加装饰音（颤音/波音/倚音/经过音等），提升旋律表现力。
    abc: 完整的 ABC Notation 字符串
    ornament_type: 装饰音类型
      - trill（颤音）：快速相邻音交替，如 cdcd
      - mordent（波音）：主音+上/下邻音+主音，如 cdc
      - grace（倚音）：短促前置音，如 {d}c
      - passing（经过音）：填充跳进间隙，如 C E → C D E
      - full（全面加花）：综合运用以上手法
    """
    from app.agentcore.llm import complete
    from app.agentcore.abc_utils import extract_abc_and_summary

    ornament_guides = {
        "trill":   "颤音：在长音上用快速相邻音交替模拟，如 c4 → cdcd，保持总时值不变",
        "mordent": "波音：主音前插入短促上/下邻音，如 c2 → dcd 或 Bc2（总时值不变）",
        "grace":   "倚音：用 {音符} 语法添加短促前置音，如 {d}c2（前置音不占时值）",
        "passing": "经过音：填充跳进间隙，如 C2 E2 → C D E2（级进替代跳进）",
        "full":    "全面加花：综合运用颤音、经过音、辅助音，密度约30-50%，保留骨干强拍音",
    }
    guide = ornament_guides.get(ornament_type.lower(), f"添加{ornament_type}装饰音，提升旋律表现力")

    prompt = f"""请为以下 ABC 谱添加装饰音，提升旋律的音乐表现力。

装饰类型：{ornament_type}
操作指南：{guide}

加花原则：
1. 必须保留原旋律的骨干音（强拍音，每小节第1、3拍）
2. 加的音必须是调式内音或和弦骨干音
3. 总时值保持不变（装饰音替换原有时值，不延长小节）
4. 所有音符保持在 Sky C4-C6 范围（C D E F G A B c d e f g a b c'）
5. 加花后每行节奏更丰富（混用附点、经过音等）

Sky ABC 装饰音语法参考：
- 颤音模拟：c4 → cdcd（快速交替）
- 经过音：C2 E2 → CDEE 或 C D E2
- 倚音：{"{d}"}c2（花括号内为倚音，不占时值）
- 波音：c2 → dcd（上波音）或 Bc2（下波音）

当前 ABC 谱：
{abc}"""

    raw = await complete(
        [{"role": "system", "content": _STYLE_SYSTEM},
         {"role": "user",   "content": prompt}],
        temperature=0.72,
    )
    new_abc, _ = extract_abc_and_summary(raw, abc)
    return new_abc


# ─── 格式转换工具（ABC ↔ MIDI ↔ Sky JSON）────────────────────────────────────

@tool(group="abc_edit")
def abc_to_midi(
    abc: str,
    output_filename: str = "",
    instrument: int = 0,
) -> dict:
    """将 ABC 谱转换为 MIDI 文件，保存到当前项目 .sky/ 目录，返回项目内相对路径。
    abc: 完整的 ABC Notation 字符串
    output_filename: 输出文件名（不含路径），留空则自动从 T: 字段生成
    instrument: GM 音色编号（0=钢琴, 25=吉他, 40=小提琴, 73=长笛）
    ⚠️ 不需要传 workspace_id / project_id，系统自动从当前会话推断，禁止猜测 ID 参数。
    """
    import re as _re
    from pathlib import Path as _Path

    # ── 1. 解析 ABC Header 获取标题 ──────────────────────────────────────────
    title = "score"
    for line in abc.splitlines():
        if line.startswith("T:"):
            title = line[2:].strip()
            break
    # 安全化文件名
    safe_title = _re.sub(r'[\\/:*?"<>|]', '_', title)[:60] or "score"
    if not output_filename:
        output_filename = f"{safe_title}.mid"
    elif not output_filename.endswith((".mid", ".midi")):
        output_filename += ".mid"

    # ── 2. ABC → Sky JSON（通过 abc_to_json.py）────────────────────────────
    try:
        from tools.abc_to_json import abc_to_cuby_json
        import json as _json
        sky_json_obj = abc_to_cuby_json(abc)
        sky_json_str = _json.dumps([sky_json_obj], ensure_ascii=False)
    except Exception as e:
        return {"ok": False, "error": f"ABC→Sky JSON 失败: {e}"}

    # ── 3. Sky JSON → QuantizedScore（通过 parser.py）──────────────────────
    try:
        from tools.parser import parse_game_score
        score = parse_game_score(sky_json_str)
    except Exception as e:
        return {"ok": False, "error": f"Sky JSON→QuantizedScore 失败: {e}"}

    # ── 4. 获取项目根目录（从 ContextVar 自动推断，不依赖 LLM 传参）──────────
    # ReactExecutor 在每次工具调用前已通过 set_current_session_id() 注入 session_id，
    # session_context.get_current_project_root() 自动查询 DB 返回项目根目录。
    try:
        from app.agentcore.session_context import get_current_project_root
        _project_root = get_current_project_root()
    except Exception:
        _project_root = ""

    if not _project_root:
        # v4.0 fix46：降级到 workspace_tools._get_project_root()（含 ws 级目录兜底）
        try:
            from app.agentcore.tools.workspace_tools import _get_project_root as _wt_root
            _fallback = _wt_root()
            if _fallback:
                _project_root = str(_fallback)
            else:
                return {"ok": False, "error": "无法确定项目根目录，请确保 session 已绑定项目"}
        except Exception:
            return {"ok": False, "error": "无法确定项目根目录，请确保 session 已绑定项目"}

    # ── 5. 写入 MIDI 文件到项目 .sky/ 目录 ──────────────────────────────────
    try:
        from tools.midi_writer import to_midi
        from pathlib import Path as _Path
        midi_dir = _Path(_project_root) / ".sky"
        midi_dir.mkdir(parents=True, exist_ok=True)
        midi_path = str(midi_dir / output_filename)
        to_midi(score, midi_path, instrument=instrument,
                add_expression=True, humanize_ticks=4)
    except Exception as e:
        return {"ok": False, "error": f"QuantizedScore→MIDI 失败: {e}"}

    ws_path = f".sky/{output_filename}"
    return {
        "ok": True,
        "workspace_path": ws_path,
        "filename": output_filename,
        "title": title,
        "note_count": len(score.notes),
        "bpm": score.bpm,
        "duration_ms": int(score.duration_ms()),
        "message": (
            f"✅ MIDI 已生成：{ws_path}（{len(score.notes)} 音符，{score.bpm:.0f} BPM）"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ABC 谱子工作区持久化（内部实现，供 Agent Runner 直接调用，不注册为工具）
# 原位于 workspace_tools.py，迁移至此以保持业务逻辑归属清晰
# ═══════════════════════════════════════════════════════════════════════════════

def save_score_to_workspace_impl(
    abc_notation: str,
    title: str = "",
    overwrite: bool = True,
    workspace_id: str = "",
) -> dict:
    """将 ABC 谱子保存到项目 .sky/ 目录（内部实现）。

    通过 ContextVar 自动推断项目根目录，无需传入 workspace_id / project_id。
    调用方：create_agent.py / convert_agent.py / edit_agent.py
    """
    import re as _re
    from app.agentcore.tools.workspace_tools import _get_project_root
    root = _get_project_root()
    if root is None:
        return {"error": "会话未绑定项目，无法保存谱子。"}

    safe_title = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title or "score").strip() or "score"
    file_name  = f"{safe_title}.abc"
    rel_path   = f".sky/{file_name}"

    sky_dir = root / ".sky"
    sky_dir.mkdir(parents=True, exist_ok=True)
    target  = sky_dir / file_name

    existed = target.exists()
    if not overwrite and existed:
        import time as _time
        ts        = int(_time.time()) % 10000
        file_name = f"{safe_title}_{ts}.abc"
        rel_path  = f".sky/{file_name}"
        target    = sky_dir / file_name

    target.write_text(abc_notation, encoding="utf-8")
    return {"path": rel_path, "existed": existed, "name": file_name}


def list_workspace_scores_impl(workspace_id: str = "") -> list[dict]:
    """列出项目 .sky/ 目录下所有 ABC 谱子文件（内部实现）。

    通过 ContextVar 自动推断项目根目录，无需传入 workspace_id。
    调用方：universal_runner.py / router.py
    """
    from app.agentcore.tools.workspace_tools import _get_project_root
    root = _get_project_root()
    if root is None:
        return []

    sky_dir = root / ".sky"
    if not sky_dir.exists():
        return []

    results = []
    for p in sorted(sky_dir.glob("*.abc")):
        stat  = p.stat()
        title = p.stem
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("T:"):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        results.append({
            "path":  f".sky/{p.name}",
            "name":  p.name,
            "title": title,
            "size":  stat.st_size,
        })
    return results

