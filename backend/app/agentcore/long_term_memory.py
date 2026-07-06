"""
长期记忆模块 — 跨会话持久化用户偏好 (v5)

解决 v4 的核心短板：session 过期（2h TTL）后用户偏好丢失。

存储内容：
  - 音乐风格偏好（"喜欢爵士风格"、"偏好慢节奏"）
  - 常用调号（"经常用C大调"）
  - 模板偏好（"喜欢 luoxiaohei 模板"）
  - 历史创作摘要（"上次创作了《晚安喵》，C大调，120BPM"）

使用方式：
  1. 每次对话结束时，提取关键信息存入长期记忆
  2. 每次对话开始时，检索相关记忆注入 system prompt

若要接入 mem0，将 add/search 替换为 mem0 client 调用：
  from mem0 import Memory
  self.mem0 = Memory()
  self.mem0.add(messages, user_id=user_id)
  results = self.mem0.search(query, user_id=user_id)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

_logger = logging.getLogger("ep_agent.ltm")

# 延迟读取 config，避免循环导入
def _get_db_path() -> Path:
    try:
        from app.config import config
        return Path(config.DATA_DIR) / "long_term_memory.db"
    except Exception:
        return Path("/tmp/ep_agent_long_term_memory.db")


class LongTermMemory:
    """
    基于 SQLite 的长期记忆（无外部依赖版本）。
    线程安全：每次操作新建连接（SQLite WAL 模式）。
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _get_db_path()
        self._init_db()

    def _init_db(self):
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        id          TEXT PRIMARY KEY,
                        user_id     TEXT NOT NULL,
                        category    TEXT NOT NULL,
                        content     TEXT NOT NULL,
                        confidence  REAL DEFAULT 1.0,
                        created_at  TEXT NOT NULL,
                        accessed_at TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_user_cat "
                    "ON memories(user_id, category)"
                )
                conn.commit()
            _logger.info("[ltm] DB 初始化完成: %s", self._db_path)
        except Exception as exc:
            _logger.warning("[ltm] DB 初始化失败（记忆功能降级）: %s", exc)

    def add(
        self,
        user_id: str,
        category: str,
        content: str,
        confidence: float = 1.0,
    ) -> bool:
        """添加一条记忆。category: style/key/template/bpm/history"""
        try:
            now = datetime.utcnow().isoformat()
            with sqlite3.connect(str(self._db_path)) as conn:
                # 同 user_id + category + content 已存在则更新 accessed_at
                existing = conn.execute(
                    "SELECT id FROM memories WHERE user_id=? AND category=? AND content=?",
                    (user_id, category, content),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE memories SET accessed_at=?, confidence=? WHERE id=?",
                        (now, confidence, existing[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO memories VALUES (?,?,?,?,?,?,?)",
                        (str(uuid.uuid4()), user_id, category, content,
                         confidence, now, now),
                    )
                conn.commit()
            return True
        except Exception as exc:
            _logger.warning("[ltm] add 失败: %s", exc)
            return False

    def search(
        self,
        user_id: str,
        query: str = "",
        category: str = "",
        limit: int = 8,
    ) -> list[dict]:
        """检索相关记忆（关键词匹配，可替换为向量检索）。"""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                if query and category:
                    rows = conn.execute(
                        "SELECT category, content, confidence FROM memories "
                        "WHERE user_id=? AND category=? AND content LIKE ? "
                        "ORDER BY accessed_at DESC LIMIT ?",
                        (user_id, category, f"%{query}%", limit),
                    ).fetchall()
                elif query:
                    rows = conn.execute(
                        "SELECT category, content, confidence FROM memories "
                        "WHERE user_id=? AND content LIKE ? "
                        "ORDER BY accessed_at DESC LIMIT ?",
                        (user_id, f"%{query}%", limit),
                    ).fetchall()
                elif category:
                    rows = conn.execute(
                        "SELECT category, content, confidence FROM memories "
                        "WHERE user_id=? AND category=? "
                        "ORDER BY accessed_at DESC LIMIT ?",
                        (user_id, category, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT category, content, confidence FROM memories "
                        "WHERE user_id=? ORDER BY accessed_at DESC LIMIT ?",
                        (user_id, limit),
                    ).fetchall()
            return [
                {"category": r[0], "content": r[1], "confidence": r[2]}
                for r in rows
            ]
        except Exception as exc:
            _logger.warning("[ltm] search 失败: %s", exc)
            return []

    def build_memory_context(self, user_id: str) -> str:
        """
        构建注入 system prompt 的记忆前缀。
        每次对话开始时调用，让 Agent 了解用户历史偏好。
        """
        memories = self.search(user_id, limit=8)
        if not memories:
            return ""

        lines = ["【用户长期记忆（跨会话偏好）】"]
        for m in memories:
            lines.append(f"  [{m['category']}] {m['content']}")
        result = "\n".join(lines)
        _logger.debug("[ltm] 注入记忆 user=%s count=%d", user_id[:8], len(memories))
        return result

    def delete_user(self, user_id: str) -> int:
        """删除指定用户的所有记忆，返回删除条数。"""
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                cur = conn.execute(
                    "DELETE FROM memories WHERE user_id=?", (user_id,)
                )
                conn.commit()
                return cur.rowcount
        except Exception as exc:
            _logger.warning("[ltm] delete_user 失败: %s", exc)
            return 0


# ── 记忆提取：对话结束后自动提取关键信息 ──────────────────────────────────────

_EXTRACT_SYSTEM = """从对话中提取用户的音乐偏好信息。
输出 JSON 数组，每条记忆包含 category 和 content：
[
  {"category": "style",    "content": "用户偏好爵士风格"},
  {"category": "key",      "content": "用户常用C大调"},
  {"category": "template", "content": "用户喜欢 luoxiaohei H5 模板"},
  {"category": "bpm",      "content": "用户偏好 BPM 80-100"},
  {"category": "history",  "content": "创作了《晚安喵》，C大调，BPM 90"}
]
若无明显偏好信息，返回空数组 []。
category 只能是：style / key / template / bpm / history
每条 content 限制在 60 字以内。
"""


async def extract_and_save_memories(
    user_id: str,
    conversation: list[dict],
    ltm: "LongTermMemory",
) -> int:
    """
    对话结束后，用 LLM 提取关键偏好信息存入长期记忆。
    在 service.universal_chat 的 finally 块中调用。
    返回：保存的记忆条数。
    """
    import re
    from app.agentcore.llm import complete

    if not conversation:
        return 0

    # 只取最近 6 条消息（节省 token）
    recent = conversation[-6:] if len(conversation) > 6 else conversation
    conv_text = "\n".join([
        f"{m.get('role','')}: {str(m.get('content',''))[:200]}"
        for m in recent
        if m.get("role") in ("user", "assistant")
    ])

    if not conv_text.strip():
        return 0

    try:
        resp = await complete([
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user",   "content": conv_text},
        ], tier="lite")

        raw = resp if isinstance(resp, str) else resp.get("content", "[]")
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            return 0

        memories = json.loads(m.group())
        saved = 0
        for mem in memories:
            cat = mem.get("category", "")
            content = mem.get("content", "").strip()
            if cat and content:
                ltm.add(user_id=user_id, category=cat, content=content)
                saved += 1

        _logger.info("[ltm] 提取并保存 %d 条记忆 user=%s", saved, user_id[:8])
        return saved

    except Exception as exc:
        _logger.warning("[ltm] 记忆提取失败（不影响主流程）: %s", exc)
        return 0


# 全局单例
long_term_memory = LongTermMemory()
