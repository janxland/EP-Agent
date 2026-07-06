"""
领域模型 - Score / Session / Export
对应原 Go 版 pipeline/domain/score.go
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ─── Score ────────────────────────────────────────────────────

@dataclass
class ScoreMeta:
    title: str = ""
    composer: str = ""
    arranged_by: str = ""
    transcribed_by: str = ""
    bpm: float = 120.0
    raw_bpm: float = 120.0
    key: str = "C"
    pitch_level: int = 0
    time_sig_num: int = 4
    time_sig_den: int = 4
    note_count: int = 0
    duration_ms: float = 0.0


@dataclass
class ABCVersion:
    version: int
    abc_notation: str
    edit_summary: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Score:
    id: str = field(default_factory=lambda: new_id("score"))
    title: str = ""
    source_json: str = ""
    source_file: str = ""
    abc_notation: str = ""
    meta: ScoreMeta = field(default_factory=ScoreMeta)
    history: list[ABCVersion] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def push_version(self, new_abc: str, summary: str):
        self.history.append(ABCVersion(
            version=len(self.history) + 1,
            abc_notation=self.abc_notation,
            edit_summary=summary,
        ))
        self.abc_notation = new_abc
        self.updated_at = datetime.now()

    def latest_version(self) -> int:
        return len(self.history) + 1


# ─── Session ──────────────────────────────────────────────────

@dataclass
class IntentRecord:
    intent: str
    intent_type: str
    summary: str
    abc_before: str
    abc_after: str
    processed_at: datetime = field(default_factory=datetime.now)


@dataclass
class Session:
    id: str = field(default_factory=lambda: new_id("sess"))
    score: Optional[Score] = None
    pipeline_state: str = "idle"   # idle / running / succeeded / failed
    context_summary: str = ""
    intent_history: list[IntentRecord] = field(default_factory=list)
    audio_history: list[dict] = field(default_factory=list)   # 音频对话历史（每轮一条记录）
    extra: dict = field(default_factory=dict)                 # 扩展字段（如 role_id，与 DB extra 列对齐）
    # 工作区/项目上下文（创建时写入，供重播引擎恢复文件隔离边界）
    workspace_id: str = ""
    project_id: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


# ─── Export ───────────────────────────────────────────────────

MIME_TYPES = {
    "abc":  "text/plain; charset=utf-8",
    "midi": "audio/midi",
    "json": "application/json",
}
