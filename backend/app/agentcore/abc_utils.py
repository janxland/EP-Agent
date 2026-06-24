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
    """统计 ABC 中的音符数量（粗略计数）。"""
    return len(re.findall(r"[A-Ga-g][',]?[0-9]*", abc))


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
