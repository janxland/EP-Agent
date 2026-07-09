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

# ── v5 双轨切换开关 ────────────────────────────────────────────────────────────
import os as _os
USE_GRAPH_ENGINE = _os.getenv("EP_AGENT_USE_GRAPH_ENGINE", "false").lower() == "true"

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
        has_score = sess.score is not None

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
        # v5 双轨切换：环境变量 EP_AGENT_USE_GRAPH_ENGINE=true 启用动态图引擎
        if USE_GRAPH_ENGINE:
            return await self._dispatch_v5(
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
            )
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
            project_id=project_id,  # NEW-07 修复：显式传入，_dispatch 是实例方法非闭包
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
        project_id: str = "",   # NEW-07 修复：从 run() 显式传入，不依赖闭包
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
            project_id=project_id,  # NEW-14 修复：传入 project_id，RunContext 不再为空
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

        # ── BUG-4 FIX: 拦截 .txt 附件+创作词但 router 未路由到 convert 的情况 ─────
        # router LLM 可能没返回 chain_intents，导致走 create 域而跳过了 convert 步骤。
        # 此时手动补 convert（因为 .txt 含 songNotes，必须先转 ABC）。
        _needs_convert_first = (
            domain != "convert"
            and attachment_name
            and attachment_name.lower().endswith(".txt")
            and attachment_content
        )
        if _needs_convert_first:
            # 快速检测附件内容是否含 songNotes（convert 域的判断逻辑）
            try:
                import json as _json, re as _re
                _raw = attachment_content.strip()
                _m = _re.search(r'\{.*\}', _raw, _re.DOTALL)
                _candidate = _m.group() if _m else _raw
                _parsed = _json.loads(_candidate)
                _arr = _parsed if isinstance(_parsed, list) else [_parsed]
                _is_sky = bool(_arr and isinstance(_arr[0], dict) and _arr[0].get("songNotes"))
                if not _is_sky:
                    # 尝试列表中的每一项
                    for _item in (_parsed if isinstance(_parsed, list) else [_parsed]):
                        if isinstance(_item, dict) and _item.get("songNotes"):
                            _is_sky = True
                            break
            except Exception:
                _is_sky = False
            if _is_sky:
                # 强制走 convert 域，不走其他域
                domain = "convert"
                await publish("pipeline.step", {
                    "step": "force_convert", "status": "running",
                    "text": f"检测到 .txt 含 Sky JSON，强制先执行 convert 步骤",
                })

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
            extra_domain = detect_chain_intent(message, chain_intents, attachment_name)
            if extra_domain in ("edit", "create", "h5_create", "h5_edit"):
                await publish("pipeline.step", {
                    "step": "chain_intent", "status": "running",
                    "text": f"检测到链式意图，继续执行：{extra_domain}",
                })
                chain_todo_mgr = TodoManager()
                chain_todo_mgr.session_id = session_id
                await chain_todo_mgr.plan(message, extra_domain, True, publish, session_id)

                # ── 关键：把 .txt 替换为 convert 生成的 .abc 文件路径 ─────────────
                # ConvertAgent._run_sky_json() 已把 ABC 落盘到 .sky/<title>.abc
                # _ws_abc_path 存在 → 后续 Agent 看到的 attachment 是 .abc 而非 .txt
                _chain_abc_path = result.get("workspace_path", "")
                _chain_ctx = ctx.with_domain(extra_domain).with_extra(
                    todo_mgr=chain_todo_mgr,
                    current_abc=result.get("abc_notation", ""),
                )
                if _chain_abc_path:
                    _chain_ctx = _chain_ctx.with_attachment_path(_chain_abc_path)

                if extra_domain in ("h5_create", "h5_edit"):
                    _att_path = attachment_workspace_path or ""
                    # BUG-05+NEW-07 修复：project_id 已作为参数传入 _dispatch
                    _chain_proj_id = project_id
                    if not _att_path and attachment_b64 and attachment_name and workspace_id:
                        _att_path = await _save_attachment_to_workspace(
                            attachment_b64, attachment_name, workspace_id, _chain_proj_id
                        )
                    _chain_ctx = _chain_ctx.with_attachment_path(_att_path)
                ChainCls = get_agent(extra_domain)
                if ChainCls:
                    return await ChainCls().run_with_ctx(_chain_ctx)
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
                    attachment_b64, attachment_name, workspace_id, project_id
                )
            if _att_ws_path and workspace_id:
                try:
                    from app.agentcore.session_context import remember_workspace_file
                    remember_workspace_file(session_id, _att_ws_path, attachment_name)
                except Exception:
                    pass
            h5_ctx = ctx.with_attachment_path(_att_ws_path)
            return await get_agent(domain)().run_with_ctx(h5_ctx)

        # ── sovits 域：先把音频附件落盘到项目目录，再分发 ──────────────────
        # 与 h5 域同等待遇：attachment_b64 或 attachment_workspace_path 均需落盘，
        # 否则 sovits_list_audio_files() 扫描磁盘找不到参考音频。
        if domain == "sovits":
            _sov_ws_path = attachment_workspace_path or ""
            # 仅当 b64 有值且磁盘路径尚未确定时才落盘（避免重复写入）
            if not _sov_ws_path and attachment_b64 and attachment_name:
                # OPT-1: 确保 ContextVar 已注入（防御性调用，避免跨 await 丢失）
                try:
                    from app.agentcore.session_context import set_current_session_id
                    set_current_session_id(session_id)
                except Exception:
                    pass
                # Fix: 不从 ctx.extra 取 project_id（刷新重建后 extra 可能为空）
                # 直接从 DB 取，此时守门已确保 project_id 写入 DB
                _sov_proj_id = project_id or ""
                if not _sov_proj_id:
                    try:
                        from app.pipeline import db as _db_sov
                        _si = _db_sov.get_session_info(session_id)
                        _sov_proj_id = (_si or {}).get("project_id") or ""
                    except Exception:
                        pass
                _sov_ws_path = await _save_attachment_to_workspace(
                    attachment_b64, attachment_name, workspace_id,
                    _sov_proj_id,
                )
                if _sov_ws_path:
                    _logger.info(
                        "[dispatch] sovits 附件已落盘: %s → %s",
                        attachment_name, _sov_ws_path,
                    )
                else:
                    # OPT-1: 落盘失败时给出明确错误，而不是静默继续
                    _logger.error(
                        "[dispatch] sovits 附件落盘失败: %s（workspace_id=%s）",
                        attachment_name, workspace_id,
                    )
                    await publish("pipeline.step", {
                        "step":   "sovits_attach_save",
                        "status": "failed",
                        "text":   f"⚠️ 参考音频保存失败（{attachment_name}），请重新上传",
                    })
                    return {
                        "error":   "attachment_save_failed",
                        "message": f"参考音频「{attachment_name}」保存失败，请重新上传或检查工作区权限",
                    }
            # 落盘成功后写入 session 重要记忆，供下一轮直接引用
            if _sov_ws_path:
                try:
                    from app.agentcore.session_context import remember_workspace_file
                    remember_workspace_file(session_id, _sov_ws_path, attachment_name)
                except Exception:
                    pass

            # OPT-2: 将 GPT-SoVITS 服务状态注入 context，让 Agent 有明确指令而非自行判断
            _sovits_status_hint = ""
            _sovits_base_url = ""
            try:
                from app.config import config as _cfg
                _sovits_base_url = getattr(_cfg, "sovits_base_url", "") or ""
            except Exception:
                pass
            if _sovits_base_url:
                try:
                    import httpx as _httpx
                    async with _httpx.AsyncClient(timeout=2.0) as _hc:
                        _r = await _hc.get(_sovits_base_url.rstrip("/") + "/")
                        _sovits_ok = _r.status_code < 500
                except Exception:
                    _sovits_ok = False
                _sovits_status_hint = (
                    "【GPT-SoVITS 服务状态：✅ 可用，优先使用 sovits 工具组】"
                    if _sovits_ok else
                    "【GPT-SoVITS 服务状态：❌ 不可用，必须使用 MiniMax 降级流程，禁止调用任何 sovits_* 工具】"
                )
            else:
                _sovits_status_hint = "【GPT-SoVITS 服务状态：❌ 未配置 SOVITS_BASE_URL，必须使用 MiniMax 降级流程】"

            sovits_ctx = ctx.with_attachment_path(_sov_ws_path)
            # 将服务状态 hint 注入 extra，供 VoiceCloneAgent 拼入 system message
            sovits_ctx = sovits_ctx.with_extra(sovits_status_hint=_sovits_status_hint)

            # OPT-3: 执行 sovits Agent，完成后检测是否有 audio 链式意图
            sovits_result = await get_agent("sovits")().run_with_ctx(sovits_ctx)

            # 检测 sovits + audio 链式意图（如"克隆后生成一首歌"）
            _chain_audio = _detect_sovits_audio_chain(message, chain_intents)
            if _chain_audio and not sovits_result.get("error"):
                _cloned_voice_path = sovits_result.get("workspace_path", "")
                _cloned_voice_id   = sovits_result.get("voice_id", "")
                await publish("pipeline.step", {
                    "step":   "chain_intent_audio",
                    "status": "running",
                    "text":   "检测到链式意图：音色克隆完成，继续生成音频...",
                })
                chain_todo_mgr = TodoManager()
                chain_todo_mgr.session_id = session_id
                await chain_todo_mgr.plan(message, "audio", has_score, publish, session_id)
                audio_ctx = ctx.with_domain("audio").with_extra(
                    todo_mgr=chain_todo_mgr,
                    cloned_voice_path=_cloned_voice_path,
                    cloned_voice_id=_cloned_voice_id,
                )
                AudioAgentCls = get_agent("audio")
                if AudioAgentCls:
                    return await AudioAgentCls().run_with_ctx(audio_ctx)

            return sovits_result

        # ── 注册表通用分发（create / audio / voice 等）───────────────────────
        AgentCls = get_agent(domain)
        if AgentCls:
            return await AgentCls().run_with_ctx(ctx)

        # ── 兜底：query（未知 domain 或注册表未命中）─────────────────────────
        _logger.warning("[dispatch] 未知 domain=%s，兜底到 QueryAgent", domain)
        return await get_agent("query")().run_with_ctx(ctx)

    async def _dispatch_v5(
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
    ) -> dict:
        """
        v5 分发：走 AgentGraph 动态图引擎。
        Supervisor 节点（LLM）决定每一步调用哪个节点，替代 if/elif 硬编码。
        通过 EP_AGENT_USE_GRAPH_ENGINE=true 启用。
        """
        # 延迟导入，避免循环依赖
        import importlib
        import sys

        # 确保所有节点已注册到图引擎（顺序：supervisor → reflect → 业务节点）
        for _mod in (
            "app.agentcore.supervisor_agent",
            "app.agentcore.agents.reflect_agent",
            "app.agentcore.agents.agent_nodes",   # convert/edit/create/h5/audio/sovits/query
        ):
            if _mod not in sys.modules:
                try:
                    importlib.import_module(_mod)
                except Exception as _e:
                    _logger.warning("[dispatch_v5] 节点模块导入失败: %s — %s", _mod, _e)

        from app.agentcore.graph_engine import AgentGraph, GraphState

        # 读取当前会话的 ABC 谱（如果有）
        _abc = ""
        try:
            sess = session_getter(session_id)
            if sess and sess.score:
                _abc = sess.score.abc_notation or ""
        except Exception:
            pass

        # 注入长期记忆上下文（用户风格/调号/BPM偏好）
        _memory_context = ""
        try:
            from app.agentcore.long_term_memory import LongTermMemory
            _memory_context = LongTermMemory().build_memory_context(session_id)
        except Exception:
            pass

        state = GraphState(
            session_id=session_id,
            workspace_id=workspace_id,
            role_id=role_id or "",
            message=message,
            attachment_name=attachment_name,
            attachment_content=attachment_content,
            attachment_workspace_path=attachment_workspace_path,
            attachment_b64=attachment_b64,
            has_score=has_score,
            abc_notation=_abc,
            publish=publish,
            session_getter=session_getter,
            session_saver=session_saver,
            convert_fn=convert_fn,
            edit_fn=edit_fn,
            todo_mgr=todo_mgr,
            memory_context=_memory_context,
        )

        _logger.info(
            "[dispatch_v5] 启动图引擎 session=%s message=%s",
            session_id[:8], message[:50],
        )

        graph = AgentGraph()
        final_state = await graph.run(state, start_node="supervisor")

        if final_state.error:
            _logger.warning("[dispatch_v5] 图执行有错误: %s", final_state.error)

        return final_state.final_output or {
            "content": final_state.error or "图引擎执行完成",
            "abc_notation": final_state.abc_notation,
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
