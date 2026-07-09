"""
abc_utils.py — ABC Notation 共享工具函数

职责（单一）：
  - 从 LLM 输出中提取 ABC 正文和 SUMMARY
  - 解析 ABC header（title/key/bpm）
  - 统计音符数量
  - 验证 ABC 结构完整性（必要 Header 字段是否齐全）
  - 估算 ABC 时长（小节数 × 拍号 / BPM）

消除重复：此前 edit_runner.py 和 universal_runner.py 各有一份 _extract_abc_and_summary，
统一到此处，所有 SubAgent 共用。
"""
from __future__ import annotations

import re

# ABC 必须包含的最小 Header 字段集合
_REQUIRED_HEADERS = {"X:", "T:", "M:", "L:", "K:"}


def extract_abc_and_summary(text: str, fallback_abc: str) -> tuple[str, str]:
    """
    从 LLM 输出中提取 ABC 正文和 SUMMARY 行。

    策略：
      1. 清理 markdown 代码块标记
      2. 找 SUMMARY: 行，提取摘要并截断
      3. 找 X: 开头的行，到截断处为 ABC 正文
      4. 验证提取的 ABC 结构完整性（X/T/M/L/K 字段）
      5. 找不到或结构不完整时返回 fallback_abc + 原文前100字
    """
    text = re.sub(r'```[a-z]*\n?', '', text).strip()

    summary = ""
    summary_match = re.search(r'SUMMARY:\s*(.+?)$', text, re.MULTILINE | re.IGNORECASE)
    if summary_match:
        summary = summary_match.group(1).strip()
        text = text[:summary_match.start()].strip()

    abc_match = re.search(r'^X:\s*\d', text, re.MULTILINE)
    if abc_match:
        abc = text[abc_match.start():].strip()
        if is_abc_structurally_valid(abc):
            return abc, summary or "修改完成"

    return fallback_abc, summary or text[:100] or "修改完成"


def is_abc_structurally_valid(abc: str) -> bool:
    """
    验证 ABC 结构完整性：检查必要 Header 字段是否齐全。
    必须包含：X: T: M: L: K:（Q: 可选）
    同时检查 Body 是否有实际音符内容。

    返回 True 表示结构完整，可安全使用。
    """
    if not abc or len(abc) < 20:
        return False

    # 检查必要 Header 字段
    for field in _REQUIRED_HEADERS:
        if not re.search(rf'^{re.escape(field)}', abc, re.MULTILINE):
            return False

    # 检查 Body 是否有音符（至少一个音符字符）
    # K: 行之后应有实际音符内容
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if k_match:
        body = abc[k_match.end():].strip()
        if not body or not re.search(r'[A-Ga-gz]', body):
            return False

    return True


def validate_abc_headers(abc: str) -> dict:
    """
    详细验证 ABC Header 字段，返回缺失字段列表。

    返回：
      {
        "valid": bool,
        "missing_fields": [...],   # 缺失的必要字段
        "has_body": bool,          # Body 是否有音符
      }
    """
    missing = []
    for field in _REQUIRED_HEADERS:
        if not re.search(rf'^{re.escape(field)}', abc, re.MULTILINE):
            missing.append(field)

    has_body = False
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if k_match:
        body = abc[k_match.end():].strip()
        has_body = bool(body and re.search(r'[A-Ga-gz]', body))

    return {
        "valid":          len(missing) == 0 and has_body,
        "missing_fields": missing,
        "has_body":       has_body,
    }


def parse_abc_header(abc: str) -> dict:
    """
    解析 ABC header，提取 title/key/bpm/time_sig。
    返回：{"title": str, "key": str, "bpm": float, "time_sig_num": int, "time_sig_den": int}
    """
    title, key, bpm = "新曲", "C", 120.0
    time_sig_num, time_sig_den = 4, 4

    for line in abc.splitlines():
        line = line.strip()
        if line.startswith("T:"):
            title = line[2:].strip() or title
        elif line.startswith("K:"):
            key = line[2:].strip() or key
        elif line.startswith("Q:"):
            try:
                bpm = float(line.split("=")[-1].strip())
            except Exception:
                pass
        elif line.startswith("M:"):
            try:
                parts = line[2:].strip().split("/")
                if len(parts) == 2:
                    time_sig_num = int(parts[0])
                    time_sig_den = int(parts[1])
            except Exception:
                pass

    return {
        "title":        title,
        "key":          key,
        "bpm":          bpm,
        "time_sig_num": time_sig_num,
        "time_sig_den": time_sig_den,
    }


def count_notes(abc: str) -> int:
    """统计 ABC Body 中的音符数量（跳过 Header 行，避免 T:/K:/C: 中的字母被误计）。"""
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    body = abc[k_match.end():] if k_match else abc
    return len(re.findall(r"[A-Ga-g][',]?[0-9]*", body))


def count_bars(abc: str) -> int:
    """
    统计 ABC 中的小节数（通过 | 分隔符计数）。
    排除 Header 行，只统计 Body 部分。
    """
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return 0
    body = abc[k_match.end():]
    # 统计 | 数量（排除 ||、|: 等特殊小节线的重复计数）
    bars = len(re.findall(r'\|(?!\|)', body))
    return max(bars, 0)


def estimate_duration_seconds(abc: str) -> float:
    """
    估算 ABC 谱子的演奏时长（秒）。
    基于小节数、拍号、BPM 计算。

    公式：时长(秒) = 小节数 × 每小节拍数 / (BPM / 60)
    """
    header = parse_abc_header(abc)
    bars   = count_bars(abc)
    if bars == 0:
        return 0.0

    beats_per_bar = header["time_sig_num"]
    bpm           = header["bpm"] or 120.0
    return bars * beats_per_bar / (bpm / 60.0)


def check_rhythm_variety(abc: str) -> dict:
    """
    检测 ABC Body 中每行的节奏多样性。

    返回：
      {
        "monotone_lines": [(line_idx, content_preview), ...],  # 纯八分音符行
        "total_body_lines": int,
        "monotone_count": int,
        "variety_ratio": float,  # 有多样性的行占比
      }
    """
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return {"monotone_lines": [], "total_body_lines": 0,
                "monotone_count": 0, "variety_ratio": 1.0}

    body = abc[k_match.end():].strip()
    lines = [l.strip() for l in body.splitlines()
             if l.strip() and not l.strip().startswith('%')]

    monotone = []
    for i, line in enumerate(lines):
        # 去掉小节线和空格，只看音符+时值
        content = re.sub(r'[|:\[\]\s]', '', line)
        # 找所有带时值标注的音符
        notes_with_dur = re.findall(r'[A-Ga-gz]\d*(?:/\d+)?', content)
        if len(notes_with_dur) < 3:
            continue
        # 判断是否全是纯八分（无时值数字）
        has_duration_mark = any(re.search(r'\d', n) for n in notes_with_dur)
        if not has_duration_mark:
            monotone.append((i, line[:50]))

    total = len(lines)
    mono_count = len(monotone)
    variety_ratio = (total - mono_count) / total if total > 0 else 1.0

    return {
        "monotone_lines": monotone,
        "total_body_lines": total,
        "monotone_count": mono_count,
        "variety_ratio": variety_ratio,
    }


def detect_duplicate_lines(abc: str) -> dict:
    """
    检测 ABC Body 中的重复旋律行。

    返回：
      {
        "has_duplicates": bool,
        "duplicate_pairs": [(line_a_idx, line_b_idx, content), ...],
        "total_lines": int,
        "unique_lines": int,
      }
    """
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return {"has_duplicates": False, "duplicate_pairs": [], "total_lines": 0, "unique_lines": 0}

    body = abc[k_match.end():].strip()
    # 按换行拆分，过滤空行和注释行
    lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith('%')]
    if not lines:
        return {"has_duplicates": False, "duplicate_pairs": [], "total_lines": 0, "unique_lines": 0}

    seen: dict[str, int] = {}
    pairs = []
    for i, line in enumerate(lines):
        # 标准化：去掉行尾注释，压缩空格
        norm = re.sub(r'%.*$', '', line).strip()
        norm = re.sub(r'\s+', ' ', norm)
        if not norm:
            continue
        if norm in seen:
            pairs.append((seen[norm], i, norm[:60]))
        else:
            seen[norm] = i

    return {
        "has_duplicates": len(pairs) > 0,
        "duplicate_pairs": pairs,
        "total_lines": len(lines),
        "unique_lines": len(seen),
    }


def check_duration_requirement(abc: str, required_minutes: float) -> dict:
    """
    检查 ABC 是否满足时长要求。

    返回：
      {
        "satisfied": bool,
        "actual_seconds": float,
        "required_seconds": float,
        "actual_bars": int,
        "required_bars": int,
        "shortage_bars": int,   # 不足时的缺口小节数（0 表示满足）
      }
    """
    required_seconds = required_minutes * 60.0
    actual_seconds   = estimate_duration_seconds(abc)
    header           = parse_abc_header(abc)
    bpm              = header["bpm"] or 120.0
    beats_per_bar    = header["time_sig_num"]

    # 估算需要的小节数
    required_bars = int(required_seconds / (beats_per_bar / (bpm / 60.0)))
    actual_bars   = count_bars(abc)
    shortage      = max(0, required_bars - actual_bars)

    return {
        "satisfied":        actual_seconds >= required_seconds * 0.85,  # 允许 15% 误差
        "actual_seconds":   actual_seconds,
        "required_seconds": required_seconds,
        "actual_bars":      actual_bars,
        "required_bars":    required_bars,
        "shortage_bars":    shortage,
    }


def extract_abc_from_message(message: str) -> str:
    """
    从用户消息中提取内嵌的 ABC 谱子。

    用户常常把参考 ABC 直接粘贴在消息里（而非作为附件上传），
    原代码完全忽略了这种情况，导致 base_abc 永远为空，改编模式永远不触发。

    支持两种格式：
      A. 多行格式（标准）：每个 header 字段独占一行，X: 开头
      B. 单行紧凑格式：X:1T:曲名M:4/4L:1/8Q:1/4=160K:C 音符... 全在一行
         （用户从 Sky 游戏导出或直接粘贴时常见此格式）

    提取策略：
      1. 快速检测：消息必须同时含 K: 和 X: 才可能有 ABC
      2. 找到 X: 的位置（字符级，不依赖行结构）
      3. 从 X: 开始截取到消息末尾（或明显的非ABC文字终止）
      4. 对紧凑单行格式，将 header 字段展开为多行（便于后续解析）
      5. 验证结构完整性（含 K: 字段 + Body 有音符）

    返回提取到的 ABC 字符串（已规范化为多行），未找到返回空字符串。
    """
    if not message:
        return ""

    # 快速检测：消息中必须同时含 K: 和 X: 才可能有 ABC
    if "K:" not in message or "X:" not in message:
        return ""

    # ── 策略A：多行格式处理 ──────────────────────────────────────────────────
    # 先尝试标准多行格式（X: 在某行行首）
    lines = message.splitlines()
    abc_start_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^X:\s*\d', stripped):
            abc_start_line = i
            break

    if abc_start_line >= 0:
        # 找到多行 X: 起始，收集 ABC 行
        _ABC_LINE_PAT = re.compile(
            r'^(?:[XTMLKQCSZS]:|'       # header 字段
            r'[A-Ga-gz\[\|\]^_,\'"!]|'  # 音符起始
            r'\|)'                       # 小节线起始
        )
        abc_lines = []
        for line in lines[abc_start_line:]:
            stripped = line.strip()
            if not stripped:
                break  # ABC 规范：空行是终止符
            if _ABC_LINE_PAT.match(stripped):
                abc_lines.append(stripped)
            else:
                has_key = any(l.startswith("K:") for l in abc_lines)
                if has_key:
                    break
        abc_text = "\n".join(abc_lines)
        if is_abc_structurally_valid(abc_text):
            return abc_text

    # ── 策略B：单行紧凑格式处理 ──────────────────────────────────────────────
    # 用户粘贴的 ABC 全在一行，如：
    #   X:1T:晚安喵M:4/4L:1/8Q:1/4=160K:Dbz8 | z6A2 | [DA_g]4...
    # 找到 X: 的字符位置
    x_pos = message.find("X:")
    if x_pos < 0:
        return ""

    raw_abc = message[x_pos:]

    # 将紧凑 header 展开为多行
    # 识别 header 字段边界：在 [A-Z]: 模式前插入换行
    # 但要避免把音符行中的大写字母误判为 header（音符行在 K: 之后）
    # 策略：先找到 K: 的位置，K: 之前的部分做 header 展开，之后保留原样

    k_pos = raw_abc.find("K:")
    if k_pos < 0:
        return ""

    # K: 之后找到调号结束（字母+可选b/#）
    # K: 调号格式：K:Db / K:C / K:Am / K:F#m 等
    # 只匹配调号本身，不能把后面的音符（z/c/d等小写字母）吃进去
    # 调号结构：根音字母(A-G) + 可选升降(b/#) + 可选调式(m/min/maj/dor等，但不是单独小写音符)
    k_end_m = re.match(r'K:[A-G][b#]?(?:m(?:in)?|maj|dor|phr|lyd|mix|aeo|loc)?', raw_abc[k_pos:])
    if not k_end_m:
        return ""
    k_end = k_pos + k_end_m.end()

    header_raw = raw_abc[:k_end]   # 从 X: 到 K:Xx 结束
    body_raw   = raw_abc[k_end:]   # K: 之后的音符内容

    # 展开 header：在每个 [A-Z]: 前插入换行（X: 本身不需要前置换行）
    header_expanded = re.sub(r'(?<!\n)([A-Z]:)', r'\n\1', header_raw).strip()

    # body 清理：去掉前导非ABC字符（如空格），保留音符和小节线
    body_clean = body_raw.strip()

    abc_text = header_expanded + "\n" + body_clean

    # 验证结构完整性
    if is_abc_structurally_valid(abc_text):
        return abc_text

    return ""


def extract_motif_bars(abc: str, bar_count: int = 8) -> str:
    """
    从 ABC 谱中提取前 N 个小节作为「核心动机段落」。

    用途：改编模式下，Python 层预提取核心动机注入 prompt，
    让 LLM 明确知道哪些是核心动机，而不是自己在长谱中猜测。

    参数：
      abc       — 完整 ABC 谱字符串
      bar_count — 提取的小节数，默认 8（约前两段）

    返回：包含 header + 前 N 小节的 ABC 片段字符串。
    """
    if not abc:
        return ""

    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return abc[:500]  # 没有 K: 行则返回前500字符

    # 提取 header（K: 行及之前）
    header = abc[:k_match.end()].strip()
    body   = abc[k_match.end():].strip()

    # 按小节线分割，收集前 bar_count 个小节
    # 小节线：| 但不是 || 或 |: 或 :|
    bars_collected = 0
    result_chars   = []
    i = 0

    while i < len(body) and bars_collected < bar_count:
        ch = body[i]
        result_chars.append(ch)
        if ch == '|':
            # 检查是否是双小节线 || 或反复记号 |: :|
            next_ch = body[i + 1] if i + 1 < len(body) else ""
            if next_ch not in ('|', ':'):
                bars_collected += 1
        i += 1

    motif_body = "".join(result_chars).strip()
    return f"{header}\n{motif_body}"


def check_melody_quality(abc: str) -> dict:
    """
    检测 ABC Body 中的旋律线条质量。

    问题：LLM 有时只输出和弦块堆砌（如 [DA_g]4[DA_g]2A2 反复），
    没有真正的旋律线条。这种输出节奏单调、音乐性极差。

    检测逻辑：
      - 统计每行中「在和弦块 [] 内的音符」占该行总音符的比例
      - 若某行 > 80% 音符都在和弦块内，判定为「纯和弦堆砌行」
      - 全曲纯和弦堆砌行占比 > 50% 时，整体质量判定为低

    返回：
      {
        "low_quality":        bool,   # True 表示旋律线条质量差
        "chord_block_lines":  [(line_idx, ratio, preview), ...],  # 问题行列表
        "total_body_lines":   int,
        "chord_block_count":  int,    # 纯和弦堆砌行数
        "quality_ratio":      float,  # 有旋律线条的行占比（越高越好）
      }
    """
    k_match = re.search(r'^K:.*$', abc, re.MULTILINE)
    if not k_match:
        return {
            "low_quality": False,
            "chord_block_lines": [],
            "total_body_lines": 0,
            "chord_block_count": 0,
            "quality_ratio": 1.0,
        }

    body = abc[k_match.end():].strip()
    lines = [l.strip() for l in body.splitlines()
             if l.strip() and not l.strip().startswith('%')]

    chord_block_lines = []
    for i, line in enumerate(lines):
        # 统计和弦块内的音符数（[] 内的字母）
        in_chord = re.findall(r'\[([^\]]+)\]', line)
        chord_note_count = sum(
            len(re.findall(r'[A-Ga-gz]', seg)) for seg in in_chord
        )
        # 统计行内所有音符数
        total_note_count = len(re.findall(r'[A-Ga-gz]', line))

        if total_note_count < 3:
            continue  # 太短的行跳过

        ratio = chord_note_count / total_note_count if total_note_count > 0 else 0.0
        if ratio > 0.80:
            chord_block_lines.append((i, ratio, line[:60]))

    total = len(lines)
    cb_count = len(chord_block_lines)
    quality_ratio = (total - cb_count) / total if total > 0 else 1.0

    return {
        "low_quality":       cb_count > 0 and quality_ratio < 0.5,
        "chord_block_lines": chord_block_lines,
        "total_body_lines":  total,
        "chord_block_count": cb_count,
        "quality_ratio":     quality_ratio,
    }
