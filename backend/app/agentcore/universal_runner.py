"""
Universal Chat Runner — 纯编排层（v3.1 精简版）

架构说明：
  此文件只负责「编排」，不包含任何执行逻辑。
  所有执行逻辑已拆分到独立模块：

  agentcore/
    todo_manager.py    — TodoManager + assert_finish_gate
    react_executor.py  — ReactExecutor + stream_text/stream_llm
    intent_router.py   — route_intent + detect_chain_intent
    abc_utils.py       — extract_abc_and_summary 等共享工具函数
    agents/
      convert_agent.py — Sky JSON → ABC 转换
      edit_agent.py    — ABC 编辑（接管 edit_runner 的 ReAct，统一 TODO 管理）
      create_agent.py  — ABC 谱创作
      audio_agent.py   — 音频/音色生成
      query_agent.py   — 谱子查询/问答

执行流程：
  1. route_intent()       → 识别 domain（轻量 LLM）
  2. TodoManager.plan()   → 并行规划 TODO（+ 异步 TodoCritic）
  3. 按 domain 调用对应 SubAgent
  4. assert_finish_gate() → finish_task 门控（对标 magic-coding-service output_contract）

扩展新意图域（只需两步）：
  Step 1: 在 agents/ 目录添加 new_agent.py（实现 run() 方法）
  Step 2: 在 _DOMAIN_AGENT_MAP 中注册
  无需修改此文件的任何执行逻辑。
"""
from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Awaitable

from app.agentcore.intent_router import route_intent, detect_chain_intent
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.role_config import get_role_or_default, DEFAULT_ROLE_ID

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
) -> str:
    """
    将 base64 附件保存到工作区，返回工作区相对路径。
    MIDI/音频等二进制文件写到 .sky/ 目录；其他写到 shared/。
    这样 H5Agent 只需传路径，LLM 不接触二进制内容。
    """
    import base64 as _b64
    from app.agentcore.tools.workspace_tools import _WS_ROOT

    ext = Path(attachment_name).suffix.lower()
    is_midi = ext in (".mid", ".midi")
    subdir = ".sky" if is_midi else "shared"

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

        # ── 获取工作区 ID 和谱子文件列表（注入上下文，跨会话感知工作区资产）────
        # 工作区 = 文件夹，谱子存在 .sky/ 目录，跨会话持久，类似 Cursor workspace
        workspace_id = ""
        workspace_scores_context = ""
        try:
            from app.pipeline import db as _db_ws
            _si = _db_ws.get_session_info(session_id)
            workspace_id = (_si or {}).get("workspace_id") or ""
            if workspace_id:
                from app.agentcore.tools.workspace_tools import list_workspace_scores_impl
                _scores = list_workspace_scores_impl(workspace_id)
                if _scores:
                    _lines = [f"  - {s['title']} ({s['name']}, {s['size']} bytes)" for s in _scores]
                    workspace_scores_context = (
                        f"\n\n【工作区谱子库（.sky/ 目录）】\n"
                        f"当前工作区已有 {len(_scores)} 首谱子：\n"
                        + "\n".join(_lines)
                        + "\n用户可以引用这些谱子进行编辑、改编或生成 H5。"
                    )
                    # 同时将谱子文件上下文注入到 session，方便子 Agent 使用
                    if not has_score and _scores:
                        # 工作区有谱子但当前 session 没有加载 → 提示用户可以直接引用
                        pass
        except Exception:
            pass

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

        # ── Step 2: TODO 规划（并行启动，不阻塞路由）──────────────────────────
        todo_mgr = TodoManager()
        todo_mgr.session_id = session_id   # 注入 session_id，tick() 回写数据库
        todos_task = asyncio.create_task(
            todo_mgr.plan(message, domain, has_score, publish, session_id)
        )

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
            todos_task=todos_task,
            has_score=has_score,
            role_id=role_id,
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
        todos_task,
        has_score: bool,
        role_id: str | None = None,
    ) -> dict:
        """按 domain 调度对应 SubAgent，处理降级和链式意图。"""
        from app.agentcore.agents.convert_agent import ConvertAgent
        from app.agentcore.agents.edit_agent    import EditAgent
        from app.agentcore.agents.create_agent  import CreateAgent
        from app.agentcore.agents.audio_agent   import AudioAgent
        from app.agentcore.agents.query_agent   import QueryAgent

        # role 对象在 _dispatch 中用于降级时重推 role.active，需在此处解析
        role = get_role_or_default(role_id)

        # ── convert 域 ────────────────────────────────────────────────────────
        if domain == "convert":
            await todos_task
            result = await ConvertAgent().run(
                session_id=session_id,
                message=message,
                attachment_content=attachment_content,
                attachment_name=attachment_name,
                publish=publish,
                convert_fn=convert_fn,
                todo_mgr=todo_mgr,
                session_getter=session_getter,
                session_saver=session_saver,
            )

            # 降级：不是合法 Sky JSON → 重新规划并路由到 create
            if not result.get("valid", True):
                await publish("pipeline.step", {
                    "step": "convert_fallback", "status": "running",
                    "text": "附件不是 Sky JSON，降级为创作模式",
                })
                fallback_todo_mgr = TodoManager()
                fallback_todo_mgr.session_id = session_id
                await fallback_todo_mgr.plan(message, "create", has_score, publish, session_id)
                return await CreateAgent().run(
                    session_id=session_id,
                    message=message,
                    publish=publish,
                    session_getter=session_getter,
                    session_saver=session_saver,
                    todo_mgr=fallback_todo_mgr,
                )

            # 链式意图：convert 成功后检测是否还有 edit/create
            # 使用全新 TodoManager 实例（避免复用已 finish_all 的实例）
            extra_domain = detect_chain_intent(message, chain_intents)
            if extra_domain in ("edit", "create", "h5_create", "h5_edit"):
                await publish("pipeline.step", {
                    "step": "chain_intent", "status": "running",
                    "text": f"检测到链式意图，继续执行：{extra_domain}",
                })
                chain_todo_mgr = TodoManager()
                chain_todo_mgr.session_id = session_id
                await chain_todo_mgr.plan(message, extra_domain, True, publish, session_id)

                if extra_domain == "edit":
                    return await EditAgent().run(
                        session_id=session_id,
                        message=message,
                        publish=publish,
                        edit_fn=edit_fn,
                        todo_mgr=chain_todo_mgr,
                        session_getter=session_getter,
                        session_saver=session_saver,
                    )
                elif extra_domain in ("h5_create", "h5_edit"):
                    from app.agentcore.agents.h5_agent import H5Agent
                    # 获取 workspace_id（chain 场景）
                    _chain_ws_id = ""
                    try:
                        from app.pipeline import db as _db_ref2
                        _chain_si = _db_ref2.get_session_info(session_id)
                        _chain_ws_id = (_chain_si or {}).get("workspace_id") or ""
                    except Exception:
                        pass
                    _chain_att_path = attachment_workspace_path or ""
                    if not _chain_att_path and attachment_b64 and attachment_name and _chain_ws_id:
                        _chain_att_path = await _save_attachment_to_workspace(
                            attachment_b64, attachment_name, _chain_ws_id
                        )
                    return await H5Agent().run(
                        session_id=session_id,
                        message=message,
                        attachment_workspace_path=_chain_att_path,
                        attachment_name=attachment_name,
                        publish=publish,
                        todo_mgr=chain_todo_mgr,
                        domain=extra_domain,
                        workspace_id=_chain_ws_id,
                    )
                else:  # create
                    return await CreateAgent().run(
                        session_id=session_id,
                        message=message,
                        publish=publish,
                        session_getter=session_getter,
                        session_saver=session_saver,
                        todo_mgr=chain_todo_mgr,
                        current_abc=result.get("abc_notation", ""),
                    )
            return result

        # ── edit 域（无谱子时降级为 create，降级后补推 role.active）────────────
        if domain == "edit":
            if not has_score:
                domain = "create"
                # 降级后重新推送 role.active（domain 已变，前端感知）
                await publish("role.active", {
                    "role_id":   role.id,
                    "role_name": role.name,
                    "icon":      role.icon,
                    "color":     role.color,
                    "_degraded": True,   # 调试标记
                })
                # 降级时取消旧的 edit 域 TODO 规划，重新规划 create 域 TODO
                # 避免前端显示「分析谱子/修改」而实际执行「创作旋律/验证」
                todos_task.cancel()
                try:
                    await todos_task  # 等待 cancel 真正生效，吃掉 CancelledError
                except asyncio.CancelledError:
                    pass
                todo_mgr = TodoManager()
                todo_mgr.session_id = session_id
                # 重新创建 task，后续 create 分支 await 的是这个新 task
                todos_task = asyncio.create_task(
                    todo_mgr.plan(message, "create", has_score, publish, session_id)
                )
            else:
                await todos_task
                return await EditAgent().run(
                    session_id=session_id,
                    message=message,
                    publish=publish,
                    edit_fn=edit_fn,
                    todo_mgr=todo_mgr,
                    session_getter=session_getter,
                    session_saver=session_saver,
                )

        # ── create 域 ─────────────────────────────────────────────────────────
        if domain == "create":
            await todos_task
            # 显式传入 session 中已有的 ABC（改编/延伸时 CreateAgent 以此为基础）
            # CreateAgent 内部也会自读 session，此处显式传入使链路意图更清晰
            _create_base_abc = ""
            try:
                _cs = session_getter(session_id)
                if _cs and _cs.score:
                    _create_base_abc = _cs.score.abc_notation or ""
            except Exception:
                pass
            return await CreateAgent().run(
                session_id=session_id,
                message=message,
                publish=publish,
                session_getter=session_getter,
                session_saver=session_saver,
                todo_mgr=todo_mgr,
                current_abc=_create_base_abc,
            )

        # ── audio / voice 域 ──────────────────────────────────────────────────
        if domain in ("audio", "voice"):
            await todos_task
            return await AudioAgent().run(
                session_id=session_id,
                message=message,
                attachment_b64=attachment_b64,
                publish=publish,
                audio_chat_fn=audio_chat_fn,
                todo_mgr=todo_mgr,
                domain=domain,
            )

        # ── h5_create / h5_edit 域 ────────────────────────────────────────────
        if domain in ("h5_create", "h5_edit"):
            from app.agentcore.agents.h5_agent import H5Agent
            await todos_task
            # 获取当前 session 的 workspace_id
            _ws_id = ""
            try:
                from app.pipeline import db as _db_ref
                _si = _db_ref.get_session_info(session_id)
                _ws_id = (_si or {}).get("workspace_id") or ""
            except Exception:
                pass
            # 优先使用前端直接传来的工作区路径（新架构：前端上传后传 workspace_path）
            # 降级：若前端传的是 base64（旧兼容路径），则在 Runner 层转存到工作区
            _att_ws_path = attachment_workspace_path or ""
            # ── 重要记忆：前端直接传来的工作区路径也登记到 Session.extra ──
            if _att_ws_path and _ws_id:
                try:
                    from app.agentcore.session_context import remember_workspace_file
                    remember_workspace_file(session_id, _att_ws_path, attachment_name)
                except Exception:
                    pass
            if not _att_ws_path and attachment_b64 and attachment_name and _ws_id:
                _att_ws_path = await _save_attachment_to_workspace(
                    attachment_b64, attachment_name, _ws_id
                )
            # ── 重要记忆：文件落盘后立即登记到 Session.extra ──
            if _att_ws_path and _ws_id:
                try:
                    from app.agentcore.session_context import remember_workspace_file
                    remember_workspace_file(session_id, _att_ws_path, attachment_name)
                except Exception:
                    pass
            return await H5Agent().run(
                session_id=session_id,
                message=message,
                attachment_workspace_path=_att_ws_path,
                attachment_name=attachment_name,
                publish=publish,
                todo_mgr=todo_mgr,
                domain=domain,
                workspace_id=_ws_id,
            )

        # ── query 域（默认兜底）───────────────────────────────────────────────
        await todos_task
        return await QueryAgent().run(
            session_id=session_id,
            message=message,
            publish=publish,
            session_getter=session_getter,
            todo_mgr=todo_mgr,
            role_id=role_id,   # ← 透传角色 ID，注入角色专属 system prompt
        )


universal_runner = UniversalChatRunner()
