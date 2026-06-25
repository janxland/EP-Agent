"""
H5 工具辅助函数 — 波形数据生成

轻量工具函数，供 h5_templates.py 和 h5_tools.py 调用。
"""
from __future__ import annotations

import math


def gen_waveform_data(notes: list[dict], bars: int = 40) -> list[float]:
    """
    根据音符时间分布生成波形可视化数据（值域 0.0~1.0）。

    notes: 音符列表，每项含 time_ms / duration_ms 字段
    bars:  波形条数（默认 40）
    返回:  长度为 bars 的浮点列表
    """
    if not notes:
        return [0.3 + 0.4 * abs(math.sin(i * 0.5)) for i in range(bars)]

    max_t = max(
        (n.get("time_ms", 0) + n.get("duration_ms", 200)) for n in notes
    )
    if max_t <= 0:
        max_t = bars * 300

    buckets = [0.0] * bars
    for note in notes:
        idx = min(int((note.get("time_ms", 0) / max_t) * bars), bars - 1)
        buckets[idx] += 1.0

    max_v = max(buckets) or 1.0

    # 三点平滑 + 归一化
    smooth = buckets[:]
    for i in range(1, bars - 1):
        smooth[i] = (buckets[i - 1] + buckets[i] * 2 + buckets[i + 1]) / 4

    return [min(1.0, max(0.05, v / max_v)) for v in smooth]
