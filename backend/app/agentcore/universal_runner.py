"""
Universal Chat Runner — 纯编排层

执行流程：
  1. route_intent()       → 识别 domain（轻量 LLM）
  2. TodoManager.plan()   → 规划 TODO
  3. 按 domain 调用对应 SubAgent
  4. assert_finish_gate() → finish_task 门控

扩展新意图域：在 agents/ 添加 new_agent.py 并在 _DOMAIN_AGENT_MAP 中注册。
"""
from __future__ import annotations

import asyncio
import importlib
import json as _json
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Awaitable

import logging as _logging

from app.agentcore.intent_router import route_intent, detect_chain_intent
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID

# ── 执行引擎：唯一路径 = v6 LangGraph ──────────────────────────────────────────
# v5（自研图引擎）和 v4（if/elif 硬编码）已废弃，全量迁移到 v6
# 保留 USE_LANGGRAPH_V2 环境变量仅用于紧急关闭（不应触发）
import os as _os
USE_LANGGRAPH_V2 = _os.getenv("EP_AGENT_USE_LANGGRAPH_V2", "true").lower() == "true"

_logger = _logging.getLogger("ep_agent.runner")

# ── 模块加载时预检 langgraph 可用性 ──────────────────────────────────────────────
try:
    from app.agentcore.graph_engine_v2 import _LANGGRAPH_AVAILABLE as _LG_AVAILABLE
except Exception:
    _LG_AVAILABLE = False

if not _LG_AVAILABLE:
    # langgraph 未安装是严重配置错误，启动时必须修复
    _logger.error(
        "[runner] ❌ langgraph 未安装！EP-Agent 需要 langgraph v6 引擎才能运行。"
        " 请立即执行: pip install langgraph>=0.2 langchain-core>=0.3"
    )
    USE_LANGGRAPH_V2 = False  # 防止 ImportError，但此时系统无法正常工作


Publisher = Callable[[str, dict], Awaitable[None]]

# ── 自动扫描注册 tools/ 目录（确保所有工具模块已 import）──────────────────────
_tools_pkg = "app.agentcore.tools"
_tools_dir = Path(__file__).parent / "tools"
for _mod_info in pkgutil.iter_modules([str(_tools_dir)]):
    _full_name = f"{_tools_pkg}.{_mod_info.name}"
    if _full_name not in sys.modules:
        try:
            importlib.import_module(_full_name)
        except Exception as _e:
            _logging.getLogger("ep_agent").warning(
                "[tools] 工具模块导入失败: %s — %s", _full_name, _e
            )


async def _save_attachment_to_workspace(
    attachment_b64: str,
    attachment_name: str,
    workspace_id: str,
    project_id: str = "",
) -> str:
    """
    将 base64 附件保存到项目目录，返回项目内相对路径。
    MIDI → .sky/{name}，其他二进制附件 → {name}（直接在项目根，无前缀）。
    完全复用 workspace_tools.write_workspace_file(encoding='base64')。
    """
    from app.agentcore.tools.workspace_tools import write_workspace_file

    ext = Path(attachment_name).suffix.lower()
    is_midi = ext in (".mid", ".midi")
    # MIDI 放 .sky/（谱子隔离区），其他文件直接放项目根（无任何前缀）
    ws_path = f".sky/{attachment_name}" if is_midi else attachment_name

    # 去掉 data URI 前缀
    b64_clean = attachment_b64.split(",", 1)[1] if "," in attachment_b64 else attachment_b64
    try:
        result = write_workspace_file(ws_path, b64_clean, encoding="base64")
        if "error" in result:
            _logger.warning("[save_attachment] 写入失败: %s", result["error"])
            return ""
    except Exception as e:
        _logger.warning("[save_attachment] 异常: %s", e)
        return ""

    return ws_path


class UniversalChatRunner:
    """
    统一对话 Runner（v3.1 纯编排版）。

    职责：路由 → 规划 TODO → 调度 SubAgent → 门控检查
    不包含任何执行逻辑（执行逻辑在各 SubAgent 中）。
    """

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_content: str,
        attachment_name: str,
        attachment_workspace_path: str = "",  # 工作区相对路径（MIDI/图片/音频）
        attachment_b64: str = "",              # 音频 base64（仅音色克隆直接上传）
        session_getter=None,
        session_saver=None,
        publish: Publisher = None,
        convert_fn=None,
        edit_fn=None,
        audio_chat_fn=None,
        role_id: str | None = None,
    ) -> dict:
        sess = session_getter(session_id)
        # BUG-036 同类修复：session_getter fallback 路径可能返回 dict，防御性处理
        if isinstance(sess, dict):
            try:
                from app.pipeline.domain import Session, Score, ScoreMeta
                _row = sess
                sess = Session(
                    id=_row.get("id", session_id),
                    workspace_id=_row.get("workspace_id", "") or "",
                    project_id=_row.get("project_id", "") or "",
                    pipeline_state=_row.get("pipeline_state", "idle") or "idle",
                    extra=_row.get("extra", {}) if isinstance(_row.get("extra"), dict) else {},
                )
                _abc = _row.get("abc_notation") or ""
                if _abc:
                    sess.score = Score(
                        title=_row.get("score_title", "") or "",
                        abc_notation=_abc,
                        meta=ScoreMeta(
                            title=_row.get("score_title", "") or "",
                            key=_row.get("score_key", "C") or "C",
                            bpm=float(_row.get("score_bpm") or 120),
                            note_count=int(_row.get("score_notes") or 0),
                        ),
                    )
            except Exception:
                sess = None
        has_score = bool(sess and sess.score)

        # ── 从 session extra 读取 role_id（若调用方未传则从 DB 恢复）──────────
        if not role_id:
            try:
                extra = getattr(sess, "extra", None) or {}
                if isinstance(extra, str):
                    extra = _json.loads(extra)
                role_id = extra.get("role_id") or DEFAULT_ROLE_ID
            except Exception:
                role_id = DEFAULT_ROLE_ID

        # 获取 workspace_id + project_id（仅用于附件保存到文件系统，不流转给工具函数）
        # 工具函数统一通过 ContextVar 推断项目根目录，无需 ID 参数
        workspace_id = ""
        project_id = ""
        try:
            from app.pipeline import db as _db_ws
            _si = _db_ws.get_session_info(session_id)
            workspace_id = (_si or {}).get("workspace_id") or ""
            project_id   = (_si or {}).get("project_id")   or ""
        except Exception:
            pass

        # 获取谱子库上下文（通过 ContextVar 推断项目根目录，无需查 DB 获取 workspace_id）
        workspace_scores_context = ""
        try:
            from app.agentcore.tools.workspace_tools import list_workspace_scores_impl
            _scores = list_workspace_scores_impl()
            if _scores:
                _lines = [f"  - {s['title']} ({s['name']}, {s['size']} bytes)" for s in _scores]
                workspace_scores_context = (
                    f"\n\n【项目谱子库（.sky/ 目录）】\n"
                    f"当前项目已有 {len(_scores)} 首谱子：\n"
                    + "\n".join(_lines)
                    + "\n用户可以引用这些谱子进行编辑、改编或生成 H5。"
                )
        except Exception as _e:
            import logging as _logging
            _logging.getLogger("ep_agent").warning(
                "[universal_runner] 谱子库上下文查询失败: %s", _e
            )

        # ── Step 1: 意图路由（传入 role_id 限制路由范围）─────────────────────
        call_id = f"call_routing_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   call_id,
            "tool":      "intent_router",
            "status":    "running",
            "arguments": {
                "message":        message[:100],
                "has_attachment": bool(attachment_name),
                "has_score":      has_score,
            },
        })

        context_summary = ""
        if sess and not isinstance(sess, dict) and sess.intent_history:
            last = sess.intent_history[-1]
            context_summary = f"最近一次操作：{last.summary}（{last.intent_type}）"
        # 工作区谱子库上下文追加到 context_summary，让意图路由和子 Agent 感知
        if workspace_scores_context:
            context_summary = context_summary + workspace_scores_context

        route = await route_intent(
            message=message,
            attachment_name=attachment_name,
            attachment_preview=attachment_content,
            has_score=has_score,
            context_summary=context_summary,
            role_id=role_id,
        )
        domain = route.get("domain", "create" if not has_score else "edit")
        chain_intents = route.get("chain_intents", [])

        await publish("tool.call", {
            "call_id":        call_id,
            "tool":           "intent_router",
            "status":         "succeeded",
            "result_preview": f"意图：{domain} — {route.get('summary', '')}",
        })
        await publish("pipeline.step", {
            "step":   "routing",
            "status": "succeeded",
            "text":   f"意图识别：{domain} — {route.get('summary', '')}",
            "domain": domain,
        })

        # ── 自动命名对话（仅首轮，title 为"新对话"时触发）──────────────────────
        await _auto_rename_session(
            session_id=session_id,
            domain=domain,
            message=message,
            summary=route.get("summary", ""),
            publish=publish,
        )

        # 推送角色信息（前端顶栏可展示当前角色）
        role = get_role_or_default(role_id)
        await publish("role.active", {
            "role_id":   role.id,
            "role_name": role.name,
            "icon":      role.icon,
            "color":     role.color,
        })

        # ── Step 2: TODO 规划（v4.0 改为同步等待，消灭并发竞态）─────────────────
        # 原来 create_task 并发：SubAgent 可能在 plan() 完成前就执行，导致竞态。
        # 改为 await 同步等待：plan() 用 lite 模型约 1-2s，流程更安全可靠。
        todo_mgr = TodoManager()
        todo_mgr.session_id = session_id
        await todo_mgr.plan(message, domain, has_score, publish, session_id)

        # ── Step 3: 按 domain 调度 SubAgent ───────────────────────────────────
        # 唯一执行路径：v6/v7 LangGraph 原生图引擎（graph_engine_v2.py）
        # v5/v4 已废弃，不再作为降级路径
        if not USE_LANGGRAPH_V2:
            # langgraph 未安装时给出明确错误，不静默降级
            _logger.error(
                "[runner] langgraph 不可用，无法处理请求。"
                " 请执行: pip install langgraph>=0.2 langchain-core>=0.3"
            )
            return {
                "error": "langgraph_unavailable",
                "content": "系统配置错误：langgraph 未安装，请联系管理员。",
            }
        # _LG_AVAILABLE 在模块加载时已确认，此处直接调用无需 try/except ImportError
        # v7：将 session_id 绑定到 todo_mgr，确保 ReactExecutor 落库时能正确关联
        todo_mgr.session_id = session_id
        return await self._dispatch_v6(
            session_id=session_id,
            message=message,
            attachment_content=attachment_content,
            attachment_name=attachment_name,
            attachment_workspace_path=attachment_workspace_path,
            attachment_b64=attachment_b64,
            publish=publish,
            session_getter=session_getter,
            session_saver=session_saver,
            convert_fn=convert_fn,
            edit_fn=edit_fn,
            todo_mgr=todo_mgr,
            has_score=has_score,
            role_id=role_id,
            workspace_id=workspace_id,
            audio_chat_fn=audio_chat_fn,
            project_id=project_id,
        )

    async def _dispatch_v6(
        self,
        session_id: str,
        message: str,
        attachment_content: str,
        attachment_name: str,
        attachment_workspace_path: str,
        attachment_b64: str,
        publish: Publisher,
        session_getter,
        session_saver,
        convert_fn,
        edit_fn,
        todo_mgr: TodoManager,
        has_score: bool,
        role_id: str | None = None,
        workspace_id: str = "",
        audio_chat_fn=None,
        project_id: str = "",
    ) -> dict:
        """
        v6 分发：走真实 langgraph 包图引擎（graph_engine_v2.py）。
        通过 EP_AGENT_USE_LANGGRAPH_V2=true 启用（默认开启）。
        此方法仅在 USE_LANGGRAPH_V2=True（模块级已预检通过）时才会被调用。
        """
        # 直接导入（模块级已预检 _LG_AVAILABLE=True，这里不会报 ImportError）
        from app.agentcore.graph_engine_v2 import stream_graph_events, EPState

        # 读取当前会话的 ABC 谱（如果有）
        _abc = ""
        try:
            sess = session_getter(session_id)
            if sess and sess.score:
                _abc = sess.score.abc_notation or ""
        except Exception:
            pass

        # 注入长期记忆上下文
        # MEM-001 修复：使用模块级单例 long_term_memory，避免每次请求重新创建 SQLite 连接
        # service.py 已正确使用单例，此处对齐保持一致
        _memory_context = ""
        try:
            from app.agentcore.long_term_memory import long_term_memory
            _memory_context = long_term_memory.build_memory_context(session_id)
        except Exception:
            pass

        # 构造 EPState（TypedDict，LangGraph 原生格式）
        state: EPState = {
            "session_id":                session_id,
            "workspace_id":              workspace_id,
            "project_id":                project_id,
            "role_id":                   role_id or "",
            "message":                   message,
            "attachment_name":           attachment_name,
            "attachment_content":        attachment_content,
            "attachment_workspace_path": attachment_workspace_path,
            "attachment_b64":            attachment_b64,
            "has_score":                 has_score,
            "abc_notation":              _abc,
            "visited":                   [],
            "visit_counts":              {},
            "tool_results":              [],
            "outputs":                   {},
            "reflection_score":          1.0,
            "reflection_notes":          "",
            "retry_count":               0,
            "final_output":              {},
            "error":                     "",
            "memory_context":            _memory_context,
            # 运行时回调（不被 Checkpointer 序列化）
            "publish":        publish,
            "session_getter": session_getter,
            "session_saver":  session_saver,
            "convert_fn":     convert_fn,
            "edit_fn":        edit_fn,
            "audio_chat_fn":  audio_chat_fn,
            "todo_mgr":       todo_mgr,
        }

        _logger.info(
            "[dispatch_v6] 启动 LangGraph v2 session=%s message=%s",
            session_id[:8], message[:50],
        )

        final_state = await stream_graph_events(state, publish, session_id)

        if final_state.get("error"):
            _logger.warning("[dispatch_v6] 图执行有错误: %s", final_state["error"])

        return final_state.get("final_output") or {
            "content":      final_state.get("error") or "LangGraph 执行完成",
            "abc_notation": final_state.get("abc_notation", ""),
        }


# ── OPT-3: sovits + audio 链式意图检测 ───────────────────────────────────────
# 检测用户是否在音色克隆的同时要求生成音频/歌曲
# 触发条件：消息中同时含有"克隆/声音"类词 和 "生成/唱/歌曲"类词
_SOVITS_CHAIN_AUDIO_KWS = [
    "生成歌曲", "生成一首", "唱一首", "生成音乐", "生成配乐",
    "克隆后生成", "用这个声音唱", "用这个声音生成", "克隆完再生成",
    "克隆声音后", "克隆好后", "然后生成", "再生成",
    "clone and generate", "clone then generate",
]


def _detect_sovits_audio_chain(message: str, chain_intents: list[str]) -> bool:
    """
    检测 sovits 域执行后是否还有 audio 链式意图。
    返回 True 表示需要继续执行 AudioAgent。
    """
    # 优先使用路由 LLM 返回的 chain_intents
    if "audio" in chain_intents:
        return True
    # 关键词快速匹配
    msg_lower = message.lower()
    return any(kw.lower() in msg_lower for kw in _SOVITS_CHAIN_AUDIO_KWS)




universal_runner = UniversalChatRunner()


# ── 自动命名对话（首轮意图识别后触发）────────────────────────────────────────────

# domain → 中文标签映射
_DOMAIN_LABEL_MAP: dict[str, str] = {
    "sovits":    "音色克隆",
    "voice":     "MiniMax音色",
    "audio":     "音频生成",
    "edit":      "谱子编辑",
    "convert":   "谱子转换",
    "query":     "问答咨询",
    "create":    "谱子创作",
    "h5_create": "H5生成",
    "h5_edit":   "H5编辑",
}

# 默认 session title（仅当 title 为此值时才自动命名，避免覆盖用户手动修改）
_DEFAULT_SESSION_TITLE = "新对话"


async def _auto_rename_session(
    session_id: str,
    domain: str,
    message: str,
    summary: str,
    publish: Publisher,
) -> None:
    """
    首轮意图识别完成后，自动将 title="新对话" 的对话命名。

    命名格式：[领域标签] 用户消息前12字
    例：[音色克隆] 帮我克隆这段声音...

    安全保障：
    - 仅当 title 为"新对话"时触发（不覆盖用户手动修改）
    - 异常完全隔离，失败不影响主流程
    """
    try:
        from app.pipeline import db as _db

        # 查询当前 session title
        session_info = _db.get_session_info(session_id)
        if not session_info:
            return
        current_title = (session_info.get("title") or "").strip()
        if current_title and current_title != _DEFAULT_SESSION_TITLE:
            # 已有自定义 title，跳过
            return

        # 生成新 title：[领域标签] 用户消息摘要
        label = _DOMAIN_LABEL_MAP.get(domain, "对话")
        # 取用户消息前12个字符，去掉换行和多余空格
        msg_snippet = message.strip().replace("\n", " ")[:12]
        if len(message.strip()) > 12:
            msg_snippet += "..."
        new_title = f"[{label}] {msg_snippet}" if msg_snippet else f"[{label}]"

        # 写入 DB
        _db.rename_session(session_id, new_title)

        # 推送 SSE 事件，前端侧边栏实时更新
        await publish("session.renamed", {
            "session_id": session_id,
            "title":      new_title,
        })

        _logger.info(
            "[auto_rename] session=%s title=%r → %r",
            session_id[:8], current_title, new_title,
        )

    except Exception as _e:
        # 命名失败完全静默，不影响主流程
        _logger.debug("[auto_rename] 命名失败（已忽略）: %s", _e)
