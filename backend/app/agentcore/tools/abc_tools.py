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
from app.agentcore.tools import tool
from app.config import config

# 注入 sky-music-tools 路径（工具层直接使用，不依赖 service.py）
if config.SKILL_DIR not in sys.path:
    sys.path.insert(0, config.SKILL_DIR)


# ─── 确定性工具（纯算法，不需要 LLM）────────────────────────

# ABC 音符到半音数的映射（C大调音阶）
_NOTE_TO_SEMITONE = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11,
    'c': 12, 'd': 14, 'e': 16, 'f': 17, 'g': 19, 'a': 21, 'b': 23,
}

_SEMITONE_TO_NOTE = {
    0: 'C', 1: '^C', 2: 'D', 3: '^D', 4: 'E', 5: 'F',
    6: '^F', 7: 'G', 8: '^G', 9: 'A', 10: '^A', 11: 'B',
}

# 调号名 → 相对 C 的半音偏移
_KEY_SEMITONES = {
    'C': 0, 'G': 7, 'D': 2, 'A': 9, 'E': 4, 'B': 11,
    'F#': 6, 'C#': 1,
    'F': 5, 'Bb': 10, 'Eb': 3, 'Ab': 8, 'Db': 1, 'Gb': 6,
    'Am': 0, 'Em': 7, 'Bm': 2, 'F#m': 9, 'C#m': 4,
    'Dm': 5, 'Gm': 10, 'Cm': 3,
}

_CHROMATIC_KEYS = ['C', '^C', 'D', '^D', 'E', 'F', '^F', 'G', '^G', 'A', '^A', 'B']


@tool
def transpose_abc(abc: str, semitones: int) -> str:
    """将 ABC 谱精确转调指定半音数（纯算法，不调用 LLM）。
    abc: 完整的 ABC Notation 字符串
    semitones: 转调半音数，正数升调，负数降调（范围 -12 到 12）。
      常用对照（从C大调出发）：G=+7, F=+5, D=+2, A=+9, E=+4, B=+11,
      F=+5, Bb=+10, Eb=+3, Ab=+8；降调用负数，如降到F=-7。
      从其他调出发时，先用 analyze_abc 获取当前调号，再计算差值。
    """
    if semitones == 0:
        return abc

    lines = abc.split('\n')
    result_lines = []
    in_body = False

    for line in lines:
        # 检测 body 开始（K: 字段之后）
        if line.startswith('K:'):
            # 更新调号
            old_key = line[2:].strip().split()[0] if line[2:].strip() else 'C'
            new_key = _shift_key(old_key, semitones)
            result_lines.append(f'K:{new_key}')
            in_body = True
            continue

        if not in_body or line.startswith('%') or not line.strip():
            result_lines.append(line)
            continue

        # 跳过 header 行（在 K: 之前）
        if re.match(r'^[A-Za-z]:', line):
            result_lines.append(line)
            continue

        # 转调音符
        result_lines.append(_transpose_note_line(line, semitones))

    return '\n'.join(result_lines)


def _shift_key(key: str, semitones: int) -> str:
    """将调号名按半音偏移"""
    base = key.rstrip('m').rstrip('#b')
    is_minor = key.endswith('m')
    semitone_val = _KEY_SEMITONES.get(key, 0)
    new_val = (semitone_val + semitones) % 12
    # 找最接近的调号名
    for k, v in _KEY_SEMITONES.items():
        if v == new_val and k.endswith('m') == is_minor:
            return k
    return _CHROMATIC_KEYS[new_val] + ('m' if is_minor else '')


def _transpose_note_line(line: str, semitones: int) -> str:
    """转调一行 ABC 音符"""
    result = []
    i = 0
    while i < len(line):
        c = line[i]
        # 前缀修饰符（升降号）
        prefix = ''
        if c in ('^', '_', '='):
            prefix = c
            i += 1
            if i >= len(line):
                result.append(prefix)
                break
            c = line[i]

        if c in _NOTE_TO_SEMITONE:
            # 计算当前音符半音值
            semitone_val = _NOTE_TO_SEMITONE[c]
            if prefix == '^':
                semitone_val += 1
            elif prefix == '_':
                semitone_val -= 1

            # 判断八度（大写=低八度，小写=高八度）
            is_upper = c.isupper()
            octave_offset = 0 if is_upper else 12

            # 转调
            new_semitone = (semitone_val + semitones) % 24
            if new_semitone < 0:
                new_semitone += 24

            # 转回音符名
            base_semitone = new_semitone % 12
            new_octave = new_semitone // 12
            note_name = _SEMITONE_TO_NOTE.get(base_semitone, 'C')

            # 处理升号前缀
            if note_name.startswith('^'):
                new_prefix = '^'
                note_name = note_name[1:]
            else:
                new_prefix = ''

            # 还原大小写（八度）
            if new_octave == 0:
                note_name = note_name.upper()
            else:
                note_name = note_name.lower()

            result.append(new_prefix + note_name)
            i += 1
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
    # 如果没有 Q: 行，在 K: 前插入
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
    """分析 ABC 谱的结构信息，返回调号、速度、拍号、音符数、音域等。
    abc: 完整的 ABC Notation 字符串
    """
    info: dict = {
        "key": "C", "bpm": 120, "time_sig": "4/4",
        "note_count": 0, "bar_count": 0,
        "range_low": None, "range_high": None,
    }
    for line in abc.split('\n'):
        if line.startswith('K:'):
            info["key"] = line[2:].strip()
        elif line.startswith('Q:'):
            m = re.search(r'=(\d+)', line)
            if m:
                info["bpm"] = int(m.group(1))
        elif line.startswith('M:'):
            info["time_sig"] = line[2:].strip()
        elif not re.match(r'^[A-Za-z]:', line) and line.strip():
            # 统计音符和小节
            info["note_count"] += len(re.findall(r'[A-Ga-g]', line))
            info["bar_count"] += line.count('|')
    return info


@tool
def get_abc_header(abc: str) -> dict:
    """提取 ABC 谱的所有 header 字段（X/T/C/A/Z/S/M/L/Q/K）。
    abc: 完整的 ABC Notation 字符串
    """
    headers: dict = {}
    for line in abc.split('\n'):
        m = re.match(r'^([A-Z]):(.*)', line)
        if m:
            headers[m.group(1)] = m.group(2).strip()
    return headers


# ─── LLM 驱动工具（今天用通用模型，未来可换垂直模型）────────
# TODO: VERTICAL_MODEL - 以下工具可替换为专业音乐模型

@tool
async def change_style(abc: str, style: str) -> str:
    """将 ABC 谱转换为指定音乐风格（爵士/古典/中国风/流行等）。
    使用 LLM 进行风格转换，未来可替换为专业音乐模型。
    abc: 完整的 ABC Notation 字符串
    style: 目标风格，如 jazz/classical/chinese/pop
    """
    # TODO: VERTICAL_MODEL - 替换为专业音乐风格转换模型
    from app.agentcore.llm import complete
    prompt = f"""你是专业的 ABC Notation 编辑器，请将以下 ABC 谱转换为{style}风格。

风格转换规则：
- jazz（爵士）：添加切分节奏、蓝调音符（降3/7音）、摇摆感
- classical（古典）：规整节奏、保留原有旋律线条
- chinese（中国风）：使用五声音阶（去掉4和7音），添加装饰音
- pop（流行）：简化节奏，增加重复段落

必须以 JSON 格式输出：
{{"new_abc": "完整修改后的 ABC 谱", "summary": "变更说明"}}

当前 ABC 谱：
{abc}"""

    raw = await complete(
        [{"role": "system", "content": "你是专业 ABC Notation 编辑器，只输出 JSON。"},
         {"role": "user", "content": prompt}],
        temperature=0.3,
    )
    import json, re as _re
    raw = _re.sub(r'```(?:json)?\s*', '', raw).strip()
    try:
        result = json.loads(raw[raw.find('{'):raw.rfind('}')+1])
        return result.get("new_abc", abc)
    except Exception:
        return abc


@tool
async def add_ornament(abc: str, ornament_type: str) -> str:
    """为 ABC 谱添加装饰音（颤音/波音/倚音等）。
    使用 LLM 添加装饰音，未来可替换为专业音乐模型。
    abc: 完整的 ABC Notation 字符串
    ornament_type: 装饰音类型，如 trill（颤音）/mordent（波音）/grace（倚音）
    """
    # TODO: VERTICAL_MODEL - 替换为专业音乐装饰音模型
    from app.agentcore.llm import complete
    prompt = f"""请为以下 ABC 谱添加{ornament_type}装饰音。
ABC 装饰音语法：~ 颤音，T 上颤音，u 倚音，. 跳音
必须以 JSON 格式输出：{{"new_abc": "...", "summary": "..."}}

ABC 谱：
{abc}"""

    raw = await complete(
        [{"role": "system", "content": "你是专业 ABC Notation 编辑器，只输出 JSON。"},
         {"role": "user", "content": prompt}],
        temperature=0.2,
    )
    import json, re as _re
    raw = _re.sub(r'```(?:json)?\s*', '', raw).strip()
    try:
        result = json.loads(raw[raw.find('{'):raw.rfind('}')+1])
        return result.get("new_abc", abc)
    except Exception:
        return abc
