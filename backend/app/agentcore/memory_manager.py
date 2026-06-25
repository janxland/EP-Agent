"""
MemoryManager — LLM 自主记忆系统

设计哲学：
  让大模型自主决定"记住什么"，而非规则硬编码。
  Agent 是有限上下文窗口的生物，必须像人一样主动管理注意力：
    - 重要的事（文件路径、用户意图、关键结论）提炼成"携带体"长期持有
    - 低价值的中间过程（工具调用细节、重复确认）动态遗忘
    - 上下文压力过大时主动压缩，保持清醒

架构：
  MemoryManager
    ├── estimate_tokens()     — 快速估算 messages token 数
    ├── should_compress()     — 判断是否需要压缩（超过阈值）
    ├── compress_messages()   — LLM 自主压缩：提炼携带体 + 裁剪历史
    ├── extract_memory()      — LLM 自主提取重要记忆写入 Session.extra
    └── build_memory_prefix() — 将 Session.extra 记忆注入新对话开头

携带体（Carrier）结构（存 Session.extra["memory"]）：
  {
    "summary":        str,   # LLM 提炼的对话摘要（最重要的事）
    "key_files":      [...], # 关键文件路径（MIDI/ABC/H5 等）
    "user_intents":   [...], # 用户核心意图历史
    "decisions":      [...], # 已做的重要决策
    "compressed_at":  int,   # 上次压缩时间戳
    "turn_count":     int,   # 已压缩了多少轮对话
  }

触发条件：
  - 每轮 ReAct 开始前检测 context 大小
  - 超过 MAX_TOKENS_RATIO（80%）时触发压缩
  - 压缩后保留 system + 最近 N 条消息 + 携带体前缀
"""
from __future__ import annotations

import json
import time
import logging
from typing import Any

logger = logging.getLogger("ep_agent")

# ── 上下文窗口配置 ────────────────────────────────────────────────────────────
# 保守估算：大多数模型 32k~128k，取 32k 作为安全下限
# 实际可通过环境变量覆盖
_DEFAULT_CTX_TOKENS  = 32_000
_COMPRESS_THRESHOLD  = 0.80   # 超过 80% 触发压缩
_KEEP_RECENT_MSGS    = 6      # 压缩后保留最近 N 条消息（不含 system）
_CHARS_PER_TOKEN     = 2.5    # 中文 token 估算（中文约 1.5~2 字/token，保守取 2.5 字/token）

# 压缩用的精简 system prompt（不占太多 token）
_COMPRESS_SYSTEM = """你是对话记忆压缩专家。
请将以下对话历史压缩为结构化的"携带体"，让后续对话能直接继承重要上下文。

压缩原则：
1. 保留：用户明确表达的意图、上传的文件路径、已生成的产物路径、重要决策
2. 丢弃：重复确认、中间过渡、工具调用的冗余细节、已完成且无需追溯的步骤
3. 文件路径必须完整保留（这是最高价值信息）
4. 用最少的字表达最多的信息

输出 JSON（严格格式，不要 markdown 代码块）：
{
  "summary": "一句话描述本次对话做了什么",
  "key_files": [
    {"path": "工作区相对路径", "type": "midi|abc|h5|json|other", "desc": "文件说明"}
  ],
  "user_intents": ["用户意图1", "用户意图2"],
  "decisions": ["重要决策1", "重要决策2"]
}"""


def estimate_tokens(messages: list[dict], extra_text: str = "") -> int:
    """
    快速估算消息列表的 token 数（字符数 / _CHARS_PER_TOKEN）。
    不调用 API，纯本地计算，用于触发压缩的粗粒度判断。
    """
    total_chars = sum(
        len(str(m.get("content") or "")) +
        len(json.dumps(m.get("tool_calls") or [], ensure_ascii=False))
        for m in messages
    )
    total_chars += len(extra_text)
    return int(total_chars / _CHARS_PER_TOKEN)


def should_compress(
    messages: list[dict],
    ctx_limit: int = _DEFAULT_CTX_TOKENS,
    threshold: float = _COMPRESS_THRESHOLD,
) -> bool:
    """
    判断是否需要压缩上下文。
    超过 ctx_limit * threshold 时返回 True。
    """
    used = estimate_tokens(messages)
    return used > ctx_limit * threshold


async def compress_messages(
    messages: list[dict],
    session_id: str = "",
    ctx_limit: int = _DEFAULT_CTX_TOKENS,
) -> list[dict]:
    """
    LLM 自主压缩消息历史。

    策略：
      1. 提取 system message（固定保留）
      2. 将中间历史（除最近 N 条外）发给 LLM 提炼携带体
      3. 将携带体写入 Session.extra["memory"]
      4. 返回：[system] + [携带体注入消息] + [最近 N 条]

    session_id: 若提供，将携带体持久化到 Session.extra
    """
    from app.agentcore.llm import complete

    if len(messages) <= _KEEP_RECENT_MSGS + 1:
        return messages  # 消息太少，不压缩

    # 分离 system 和对话历史
    system_msgs = [m for m in messages if m.get("role") == "system"]
    dialog_msgs = [m for m in messages if m.get("role") != "system"]

    if len(dialog_msgs) <= _KEEP_RECENT_MSGS:
        return messages

    # 需要压缩的历史（排除最近 N 条）
    to_compress = dialog_msgs[:-_KEEP_RECENT_MSGS]
    to_keep     = dialog_msgs[-_KEEP_RECENT_MSGS:]

    # 构建压缩请求（精简格式，只传文本内容）
    history_text = _format_for_compression(to_compress)

    try:
        compress_resp = await complete(
            messages=[
                {"role": "system",  "content": _COMPRESS_SYSTEM},
                {"role": "user",    "content": f"请压缩以下对话历史：\n\n{history_text}"},
            ],
            temperature=0.1,
        )
        carrier = _parse_carrier(compress_resp)
    except Exception as e:
        logger.warning("[memory] 压缩失败，保持原始消息: %s", e)
        return messages

    # 持久化携带体到 Session.extra
    if session_id and carrier:
        _save_carrier(session_id, carrier)

    # 构建压缩后的消息列表
    carrier_msg = _build_carrier_message(carrier)
    compressed = system_msgs + [carrier_msg] + to_keep

    tokens_before = estimate_tokens(messages)
    tokens_after  = estimate_tokens(compressed)
    logger.info(
        "[memory] 压缩完成: %d→%d tokens (%.0f%% 节省), session=%s",
        tokens_before, tokens_after,
        (1 - tokens_after / max(tokens_before, 1)) * 100,
        session_id[:8] if session_id else "?",
    )
    return compressed


async def extract_memory_from_result(
    session_id: str,
    tool_name: str,
    tool_result: dict,
) -> None:
    """
    工具执行后，LLM 自主判断是否有值得记忆的信息。
    当前实现：规则优先（文件路径高价值，直接记），
    未来可升级为 LLM 自主评估。

    高价值信息自动记忆：
      - workspace_path / url_path / file_path → 写入 key_files
      - midi_url → 写入 key_files
    """
    if not session_id or not tool_result:
        return

    # 提取文件路径类高价值信息
    file_entries = []

    ws_path = tool_result.get("workspace_path", "")
    if ws_path:
        file_entries.append({
            "path": ws_path,
            "type": _infer_type(ws_path),
            "desc": f"由 {tool_name} 生成",
            "ts":   int(time.time()),
        })

    midi_url = tool_result.get("midi_url", "")
    if midi_url and not midi_url.startswith("http"):
        # 相对路径 MIDI（如 ../.sky/xxx.mid），提取工作区路径
        clean = midi_url.lstrip("./").lstrip("../")
        file_entries.append({
            "path": clean,
            "type": "midi",
            "desc": f"MIDI 文件（{tool_name} 引用）",
            "ts":   int(time.time()),
        })

    if not file_entries:
        return

    _append_key_files(session_id, file_entries)


def build_memory_prefix(session_id: str) -> str:
    """
    构建记忆前缀文本，注入到新对话的 system message 末尾。

    数据来源（双轨合并）：
      1. Session.extra["memory"]          — LLM 压缩后的携带体（对话轮次多时才有）
      2. Session.extra["workspace_files"] — 规则自动写入的文件路径（每次工具执行后即有）
    两者合并去重，确保即使从未触发压缩，文件路径也能注入 Agent context。

    返回空字符串表示无记忆或记忆为空。
    """
    carrier = _load_carrier(session_id)

    # ── 补充：从 workspace_files 读取规则写入的文件路径 ──────────────────────
    ws_file_lines: list[str] = []
    try:
        from app.agentcore.session_context import is_context_set, ctx_get_session
        if is_context_set():
            sess = ctx_get_session(session_id)
            if sess:
                extra = sess.extra if isinstance(sess.extra, dict) else {}
                ws_files = extra.get("workspace_files", {})
                carrier_paths = {
                    f["path"] for f in (carrier.get("key_files", []) if carrier else [])
                }
                for ftype, flist in ws_files.items():
                    for f in flist[:5]:  # 每类最多 5 条
                        p = f.get("path", "")
                        if p and p not in carrier_paths:
                            ws_file_lines.append(
                                f"- `{p}` ({ftype}) — {f.get('name', '')}"
                            )
                            carrier_paths.add(p)
    except Exception:
        pass

    if not carrier and not ws_file_lines:
        return ""

    parts = ["\n\n---\n## 📌 重要记忆（历史上下文携带体）\n"]

    if carrier:
        summary = carrier.get("summary", "")
        if summary:
            parts.append(f"**摘要**：{summary}\n")

        key_files = carrier.get("key_files", [])
        if key_files:
            parts.append("\n**关键文件（LLM提炼）**：")
            for f in key_files[:10]:
                parts.append(f"\n- `{f['path']}` ({f.get('type','?')}) — {f.get('desc','')}")

        user_intents = carrier.get("user_intents", [])
        if user_intents:
            parts.append(f"\n\n**用户意图**：{' / '.join(user_intents[:3])}")

        decisions = carrier.get("decisions", [])
        if decisions:
            parts.append(f"\n\n**已决策**：{' / '.join(decisions[:3])}")

    if ws_file_lines:
        parts.append("\n\n**工作区文件（自动记录）**：\n" + "\n".join(ws_file_lines))

    parts.append("\n---")
    return "".join(parts)


# ── 内部工具函数 ──────────────────────────────────────────────────────────────

def _format_for_compression(messages: list[dict]) -> str:
    """将消息列表格式化为可读文本（用于发给 LLM 压缩）。"""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = str(m.get("content") or "")[:500]  # 截断超长内容
        tool_calls = m.get("tool_calls") or []
        if tool_calls:
            tc_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            lines.append(f"[{role}] (调用工具: {', '.join(tc_names)}) {content[:100]}")
        elif role == "tool":
            lines.append(f"[tool:{m.get('tool_name','?')}] {content[:200]}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _parse_carrier(llm_response: str) -> dict:
    """解析 LLM 返回的携带体 JSON。"""
    try:
        # 去除可能的 markdown 代码块
        text = llm_response.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rsplit("```", 1)[0]
        return json.loads(text)
    except Exception:
        # 解析失败：返回最小携带体
        return {
            "summary":      llm_response[:200],
            "key_files":    [],
            "user_intents": [],
            "decisions":    [],
        }


def _build_carrier_message(carrier: dict) -> dict:
    """将携带体转为 assistant 消息，注入压缩后的消息列表。"""
    text_parts = ["[对话历史已压缩，以下为携带体摘要]"]
    if carrier.get("summary"):
        text_parts.append(f"摘要：{carrier['summary']}")
    if carrier.get("key_files"):
        paths = [f['path'] for f in carrier['key_files']]
        text_parts.append(f"关键文件：{', '.join(paths)}")
    if carrier.get("user_intents"):
        text_parts.append(f"用户意图：{' / '.join(carrier['user_intents'])}")
    return {
        "role":    "assistant",
        "content": "\n".join(text_parts),
    }


def _infer_type(path: str) -> str:
    """从文件路径推断类型。"""
    import os
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mid": "midi", ".midi": "midi",
        ".abc": "abc",  ".txt":  "abc",
        ".json": "json",
        ".html": "h5",
        ".mp3": "audio", ".wav": "audio", ".ogg": "audio",
    }.get(ext, "other")


def _load_carrier(session_id: str) -> dict:
    """从 Session.extra 读取携带体。"""
    try:
        from app.agentcore.session_context import is_context_set, ctx_get_session
        if not is_context_set():
            return {}
        sess = ctx_get_session(session_id)
        if sess is None:
            return {}
        extra = sess.extra if isinstance(sess.extra, dict) else {}
        return extra.get("memory", {})
    except Exception:
        return {}


def _save_carrier(session_id: str, carrier: dict) -> None:
    """将携带体持久化到 Session.extra["memory"]。"""
    try:
        from app.agentcore.session_context import is_context_set, ctx_get_session, ctx_save_session
        if not is_context_set():
            return
        sess = ctx_get_session(session_id)
        if sess is None:
            return
        extra = sess.extra if isinstance(sess.extra, dict) else {}
        # 合并已有携带体（保留 key_files 历史，更新其他字段）
        existing = extra.get("memory", {})
        merged_files = existing.get("key_files", [])
        new_files = carrier.get("key_files", [])
        # 去重合并（path 为 key）
        existing_paths = {f["path"] for f in merged_files}
        for f in new_files:
            if f.get("path") and f["path"] not in existing_paths:
                merged_files.append(f)
                existing_paths.add(f["path"])
        merged_files = merged_files[-20:]  # 最多保留 20 个文件记录

        extra["memory"] = {
            **carrier,
            "key_files":     merged_files,
            "compressed_at": int(time.time()),
            "turn_count":    existing.get("turn_count", 0) + 1,
        }
        sess.extra = extra
        ctx_save_session(sess)
    except Exception as e:
        logger.warning("[memory] 携带体保存失败: %s", e)


def _append_key_files(session_id: str, file_entries: list[dict]) -> None:
    """向 Session.extra["memory"]["key_files"] 追加文件记录（去重）。"""
    try:
        from app.agentcore.session_context import is_context_set, ctx_get_session, ctx_save_session
        if not is_context_set():
            return
        sess = ctx_get_session(session_id)
        if sess is None:
            return
        extra = sess.extra if isinstance(sess.extra, dict) else {}
        memory = extra.get("memory", {})
        key_files = memory.get("key_files", [])
        existing_paths = {f["path"] for f in key_files}
        for entry in file_entries:
            if entry.get("path") and entry["path"] not in existing_paths:
                key_files.append(entry)
                existing_paths.add(entry["path"])
        key_files = key_files[-20:]
        memory["key_files"] = key_files
        extra["memory"] = memory
        sess.extra = extra
        ctx_save_session(sess)
    except Exception as e:
        logger.warning("[memory] key_files 追加失败: %s", e)
