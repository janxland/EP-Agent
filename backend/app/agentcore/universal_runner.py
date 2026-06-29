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
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Awaitable

import logging as _logging
import uuid as _uuid

from app.agentcore.intent_router import route_intent, detect_chain_intent
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID

_logger = _logging.getLogger("ep_agent.runner")

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
            import logging as _logging
            _logging.getLogger("ep_agent").warning(
                "[tools] 工具模块导入失败: %s — %s", _full_name, _e
            )


async def _save_attachment_to_workspace(
    attachment_b64: str,
    attachment_name: str,
    workspace_id: str,
    project_id: str = "",
) -> str:
    """将 base64 附件保存到项目目录，返回项目内相对路径（MIDI→.sky/，其他→shared/）。"""
    import base64 as _b64
    from app.agentcore.tools.workspace_tools import _WS_ROOT

    ext = Path(attachment_name).suffix.lower()
    is_midi = ext in (".mid", ".midi")
    subdir = ".sky" if is_midi else "shared"

    if project_id:
        ws_dir = _WS_ROOT / workspace_id / "projects" / project_id / subdir
    else:
        ws_dir = _WS_ROOT / workspace_id / subdir
    ws_dir.mkdir(parents=True, exist_ok=True)
    dest = ws_dir / attachment_name

    # 去掉 data URI 前缀
    b64_clean = attachment_b64.split(",", 1)[1] if "," in attachment_b64 else attachment_b64
    try:
        raw = _b64.b64decode(b64_clean)
        dest.write_bytes(raw)
    except Exception:
        return ""

    ws_path = f"{subdir}/{attachment_name}"
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
        has_score = sess.score is not None

        # ── 从 session extra 读取 role_id（若调用方未传则从 DB 恢复）──────────
        if not role_id:
            try:
                import json as _json
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

        # ── 提前注入 ContextVar（fix38）+ M3 Trace ID（fix40）─────────────────
        # list_workspace_scores_impl() 等工具在 ReactExecutor 启动前就被调用，
        # 必须在此处提前注入 session_id，否则 _get_project_root() 返回 None。
        # trace_id：每请求唯一，全链路日志均携带，便于聚合排查。
        if session_id:
            try:
                from app.agentcore.session_context import (
                    set_current_session_id,
                    set_current_trace_id,
                )
                set_current_session_id(session_id)
                _trace_id = _uuid.uuid4().hex
                set_current_trace_id(_trace_id)
                _logger.info(
                    "[trace=%s] 请求开始 session=%s msg=%s",
                    _trace_id[:8], session_id[:8],
                    message[:50].replace("\n", " "),
                )
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
        if sess.intent_history:
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
        })

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
        return await self._dispatch(
            domain=domain,
            chain_intents=chain_intents,
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
            audio_chat_fn=audio_chat_fn,
            todo_mgr=todo_mgr,
            has_score=has_score,
            role_id=role_id,
            workspace_id=workspace_id,
        )

    async def _dispatch(
        self,
        domain: str,
        chain_intents: list[str],
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
        audio_chat_fn,
        todo_mgr: TodoManager,
        has_score: bool,
        role_id: str | None = None,
        workspace_id: str = "",
    ) -> dict:
        """
        按 domain 调度对应 SubAgent（v4.0 注册表分发）。

        分发优先级：
          1. convert 域（含降级 + 链式意图）→ 内联处理
          2. edit 域（无谱子时降级为 create）→ 内联处理
          3. h5 域（需先保存附件）→ 内联处理
          4. 注册表 get_agent(domain) → run_with_ctx(ctx)
          5. 兜底 → QueryAgent
        """
        from app.agentcore.agent_registry import get_agent, ensure_all_agents_loaded
        from app.agentcore.run_context import RunContext

        # 确保所有 Agent 模块已加载（触发 @register 装饰器）
        ensure_all_agents_loaded()

        # role 对象在降级时重推 role.active
        role = get_role_or_default(role_id)

        # ── 构造统一 RunContext（贯穿全链路）─────────────────────────────────
        # extra 携带旧接口所需回调，供各 Agent 的 run_with_ctx 解包
        ctx = RunContext(
            session_id=session_id,
            workspace_id=workspace_id,
            role_id=role_id or "",
            message=message,
            attachment_content=attachment_content,
            attachment_name=attachment_name,
            attachment_workspace_path=attachment_workspace_path,
            attachment_b64=attachment_b64,
            has_score=has_score,
            domain=domain,
            publish=publish,
            extra={
                "session_getter": session_getter,
                "session_saver":  session_saver,
                "convert_fn":     convert_fn,
                "edit_fn":        edit_fn,
                "audio_chat_fn":  audio_chat_fn,
                "todo_mgr":       todo_mgr,
            },
        )

        # ── convert 域（含降级 + 链式意图，逻辑复杂保留内联）────────────────
        if domain == "convert":
            result = await get_agent("convert")().run_with_ctx(ctx)

            # 降级：不是合法 Sky JSON → 重新规划并路由到 create
            if not result.get("valid", True):
                await publish("pipeline.step", {
                    "step": "convert_fallback", "status": "running",
                    "text": "附件不是 Sky JSON，降级为创作模式",
                })
                fallback_todo_mgr = TodoManager()
                fallback_todo_mgr.session_id = session_id
                await fallback_todo_mgr.plan(message, "create", has_score, publish, session_id)
                fallback_ctx = ctx.with_domain("create").with_extra(todo_mgr=fallback_todo_mgr)
                return await get_agent("create")().run_with_ctx(fallback_ctx)

            # 链式意图：convert 成功后检测是否还有 edit/create/h5
            extra_domain = detect_chain_intent(message, chain_intents)
            if extra_domain in ("edit", "create", "h5_create", "h5_edit"):
                await publish("pipeline.step", {
                    "step": "chain_intent", "status": "running",
                    "text": f"检测到链式意图，继续执行：{extra_domain}",
                })
                chain_todo_mgr = TodoManager()
                chain_todo_mgr.session_id = session_id
                await chain_todo_mgr.plan(message, extra_domain, True, publish, session_id)
                chain_ctx = ctx.with_domain(extra_domain).with_extra(
                    todo_mgr=chain_todo_mgr,
                    current_abc=result.get("abc_notation", ""),
                )
                if extra_domain in ("h5_create", "h5_edit"):
                    _att_path = attachment_workspace_path or ""
                    if not _att_path and attachment_b64 and attachment_name and workspace_id:
                        _att_path = await _save_attachment_to_workspace(
                            attachment_b64, attachment_name, workspace_id, ""
                        )
                    chain_ctx = chain_ctx.with_attachment_path(_att_path)
                ChainCls = get_agent(extra_domain)
                if ChainCls:
                    return await ChainCls().run_with_ctx(chain_ctx)
            return result

        # ── edit 域（无谱子时降级为 create）──────────────────────────────────
        if domain == "edit":
            if not has_score:
                domain = "create"
                await publish("role.active", {
                    "role_id":   role.id,
                    "role_name": role.name,
                    "icon":      role.icon,
                    "color":     role.color,
                    "_degraded": True,
                })
                # v4.0: plan() 已同步完成，降级时直接重新规划（无竞态）
                fallback_todo_mgr = TodoManager()
                fallback_todo_mgr.session_id = session_id
                await fallback_todo_mgr.plan(message, "create", has_score, publish, session_id)
                fallback_ctx = ctx.with_domain("create").with_extra(todo_mgr=fallback_todo_mgr)
                return await get_agent("create")().run_with_ctx(fallback_ctx)
            return await get_agent("edit")().run_with_ctx(ctx)

        # ── h5 域（需先保存附件到工作区）─────────────────────────────────────
        if domain in ("h5_create", "h5_edit"):
            _att_ws_path = attachment_workspace_path or ""
            if not _att_ws_path and attachment_b64 and attachment_name and workspace_id:
                _att_ws_path = await _save_attachment_to_workspace(
                    attachment_b64, attachment_name, workspace_id, ""
                )
            if _att_ws_path and workspace_id:
                try:
                    from app.agentcore.session_context import remember_workspace_file
                    remember_workspace_file(session_id, _att_ws_path, attachment_name)
                except Exception:
                    pass
            h5_ctx = ctx.with_attachment_path(_att_ws_path)
            return await get_agent(domain)().run_with_ctx(h5_ctx)

        # ── 注册表通用分发（create / audio / voice / sovits 等）─────────────
        AgentCls = get_agent(domain)
        if AgentCls:
            return await AgentCls().run_with_ctx(ctx)

        # ── 兜底：query（未知 domain 或注册表未命中）─────────────────────────
        _logger.warning("[dispatch] 未知 domain=%s，兜底到 QueryAgent", domain)
        return await get_agent("query")().run_with_ctx(ctx)


universal_runner = UniversalChatRunner()
