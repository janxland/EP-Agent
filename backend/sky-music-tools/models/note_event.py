"""
标准化音符事件数据结构
所有工具之间传递的通用格式
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NoteEvent:
    """单个音符事件"""
    tick: int           # 量化后的 MIDI tick（基于 PPQ=480）
    pitch: int          # MIDI 音高 0-127（60=C4）
    duration: int       # tick 时长
    velocity: int = 80  # 力度 0-127
    channel: int = 0    # MIDI 声部 0-15


@dataclass
class QuantizedScore:
    """量化后的完整乐谱"""
    title: str               # JSON: name
    composer: str            # JSON: author
    bpm: float               # 实际演奏 BPM（raw_bpm / 2）
    time_sig_num: int        # 拍号分子，如 4
    time_sig_den: int        # 拍号分母，如 4
    ppq: int                 # Pulses Per Quarter（时间分辨率）
    key: str                 # 调性，如 "Eb", "C"（JSON: pitch）
    pitch_offset: int        # 移调半音数（pitch + pitchLevel）
    notes: list              # List[NoteEvent]
    raw_bpm: float = 240.0   # 原始游戏 BPM（JSON: bpm）
    arranged_by: str = ""    # JSON: arrangedBy
    transcribed_by: str = "" # JSON: transcribedBy
    bits_per_page: int = 16  # JSON: bitsPerPage（影响每行小节数）
    pitch_level: int = 0     # JSON: pitchLevel

    def duration_ms(self) -> float:
        """乐谱总时长（毫秒）"""
        if not self.notes:
            return 0.0
        last = max(self.notes, key=lambda n: n.tick + n.duration)
        ms_per_tick = (60000.0 / self.bpm) / self.ppq
        return (last.tick + last.duration) * ms_per_tick
