"""
EditAgent — ABC 编辑 SubAgent（v3.1）

职责（单一）：
  - 从 session 中取当前谱子 + 元数据
  - 调用 run_edit()（edit_runner 的纯逻辑层）执行 ReAct 编辑
  - 统一管理 edit 域 TODO 状态（complete_one 纪律）
  - 编辑结果落库 + 推送 abc.updated
  - 异常路径 finish_all(failed) + assert_finish_gate

架构改进（v3.1）：
  v3.0：EditAgent 调用 edit_fn(session_id, message, publish)，edit_fn 内嵌独立 ReAct Loop
  v3.1：EditAgent 直接调用 run_edit()，ReactExecutor 统一执行 ReAct Loop
        edit_runner.py 只保留 ABC 编辑专用逻辑（prompt 构造 + OutputAdapter）
        彻底消除"绕过 TodoManager"的架构问题
"""
from __future__ import annotations

from typing import Callable, Awaitable, TYPE_CHECKING

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agents.base_agent import BaseAgent
from app.agentcore.react_executor import stream_text
from app.agentcore.abc_utils import parse_abc_header, count_notes
from app.agentcore.agent_registry import register

if TYPE_CHECKING:
    from app.agentcore.run_context import RunContext

Publisher = Callable[[str, dict], Awaitable[None]]


@register("edit")
class EditAgent(BaseAgent):
    """
    ABC 编辑 SubAgent。

    执行流程：
      1. 从 session 取当前谱子（无谱子则报错）
      2. tick TODO[0] → running
      3. 调用 run_edit()（委托 ReactExecutor 执行 ReAct Loop）
      4. complete_one TODO[0] → done（真实落地后）
      5. 编辑结果落库 + 推送 abc.updated
      6. finish_all + assert_finish_gate
    """

    async def _run_impl(
        self,
        session_id: str,
        message: str,
        publish: Publisher,
        edit_fn: Callable,          # 保留参数签名兼容性，v3.1 不再使用
        todo_mgr: TodoManager,
        session_getter: Callable,
        session_saver: Callable,
        workspace_id: str = "",     # 工作区 ID，留空时自动从 DB 查询
    ) -> dict:
        # ── 从 session 取当前谱子 ─────────────────────────────────────────────
        # BUG-036 修复：session_getter fallback 路径（_db.get_session_info）返回 dict，
        # 需将 dict 重建为 Session 对象，避免 AttributeError: 'dict' has no attribute 'score'
        sess = session_getter(session_id)
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
            except Exception as _conv_err:
                import logging as _log
                _log.getLogger("ep_agent.edit_agent").warning(
                    "[BUG-036] session dict→Session 转换失败 session=%s: %s",
                    session_id[:8], _conv_err,
                )
                sess = None
        # 解耦设计：session 无谱子时，不做 Python 层 regex 提取。
        # 用户粘贴的 ABC 在 message 里，LLM 完全有能力从上下文中识别和理解 ABC 结构。
        # 直接把 current_abc='' 传给 edit_runner，由 _build_user_prompt 告知 LLM
        # 从用户消息中寻找 ABC 并按意图修改后输出。
        # （只有落库时才需要从 LLM 输出中提取 ABC，extract_abc_and_summary 在 edit_runner 中完成）
        if sess and sess.score:
            current_abc = sess.score.abc_notation
            meta        = sess.score.meta
        else:
            current_abc = ""
            meta        = None

        # 注入历史上下文（让 LLM 感知上次做了什么）
        context_summary = ""
        if sess and sess.intent_history:
            last = sess.intent_history[-1]
            context_summary = f"上次操作：{last.summary}（{last.intent_type}）"

        # ── TODO 纪律：开始执行时 tick running ───────────────────────────────
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        edit_call_id = f"call_edit_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   edit_call_id,
            "tool":      "abc_editor",
            "status":    "running",
            "arguments": {"intent": message[:100]},
        })

        # ── 调用 run_edit()（v3.1：直接调用，不再透传 edit_fn）──────────────
        # current_abc 为空时，edit_runner._build_user_prompt 会告知 LLM 从消息中理解 ABC
        try:
            from app.agentcore.edit_runner import run_edit
            result = await run_edit(
                intent=message,
                current_abc=current_abc,
                meta=meta,
                context_summary=context_summary,
                publish=publish,
                todo_mgr=todo_mgr,
                scene="editor",
                session_id=session_id,
            )
        except Exception as e:
            await publish("tool.call", {
                "call_id": edit_call_id, "tool": "abc_editor",
                "status": "failed", "error": str(e),
            })
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "edit", publish)
            reply = f"编辑失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "edit", "message": reply, "abc_updated": False}

        new_abc = result.get("abc", current_abc or "")
        summary = result.get("summary", "修改完成")

        # LLM 从消息中理解并输出了 ABC，同步写入 session 供后续轮次使用
        if not current_abc and new_abc:
            import logging as _log
            _log.getLogger("ep_agent.edit_agent").info(
                "[edit_agent] 从 LLM 输出获得 ABC（%d chars），写入 session", len(new_abc)
            )
            try:
                from app.pipeline.domain import Session
                if sess is None:
                    sess = Session(id=session_id)
            except Exception:
                pass

        await publish("tool.call", {
            "call_id":        edit_call_id,
            "tool":           "abc_editor",
            "status":         "succeeded",
            "result_preview": summary,
        })

        # ── TODO 纪律：真实落地后 complete_one ───────────────────────────────
        # BUG-E1 修复：检查 ReactExecutor 是否超轮退出（exceeded_rounds=True）
        # 超轮时任务未完整执行，应 finish_all(failed) 而非 done，避免掩盖问题
        _timed_out = result.get("exceeded_rounds", False)
        if _timed_out:
            await publish("pipeline.step", {
                "step": "edit_timeout", "status": "warning",
                "text": f"⚠️ 编辑超过最大轮次（{result.get('rounds', '?')} 轮），任务可能未完整执行",
            })
            await todo_mgr.finish_all(publish, "failed")
        else:
            # ReactExecutor 在 ReAct Loop 内部已经 complete_one 了 running TODO，
            # 此处只需 finish_all 收尾剩余 pending TODO（如"验证结果"等）
            await todo_mgr.finish_all(publish, "done")

        # ── 落库 + 推送 abc.updated ───────────────────────────────────────────
        # 无论 current_abc 是否为空，只要 LLM 输出了有效 ABC 就落库
        if new_abc:
            try:
                from app.pipeline.domain import Score, ScoreMeta, Session

                header = parse_abc_header(new_abc)
                new_meta = ScoreMeta(
                    title        = header["title"],
                    key          = header["key"],
                    bpm          = header["bpm"],
                    note_count   = count_notes(new_abc),
                    time_sig_num = header.get("time_sig_num", 4),
                    time_sig_den = header.get("time_sig_den", 4),
                )
                new_score = Score(
                    title        = header["title"],
                    abc_notation = new_abc,
                    meta         = new_meta,
                )
                if sess is None:
                    sess = Session(id=session_id)
                sess.score = new_score
                session_saver(sess)
            except Exception:
                pass

        # ── 自动写入工作区文件（.sky/<title>.abc）─────────────────────────────
        try:
            from app.agentcore.tools.abc_tools import save_score_to_workspace_impl  # 业务逻辑归属 abc_tools
            _new_header = parse_abc_header(new_abc)
            _save_result = save_score_to_workspace_impl(
                abc_notation=new_abc,
                title=_new_header["title"] or "score",
                overwrite=True,
            )
            # 写入重要记忆：ABC 文件路径（供 H5Agent 等跨轮次感知）
            try:
                from app.agentcore.session_context import remember_workspace_file
                remember_workspace_file(session_id, _save_result["path"],
                                       _new_header["title"] or "score")
            except Exception:
                pass
            await publish("workspace.file_saved", {
                "path": _save_result["path"],
                "type": "abc",
                "title": _new_header["title"],
            })
        except Exception:
            pass

        # 从新 ABC 重新解析 header（转调/变速后 key/bpm 已变，必须用新值）
        new_header = parse_abc_header(new_abc) if new_abc else {}
        # BUG-04 修复：简化脆弱的 getattr 链，改用安全的 try/except
        _version = 2
        try:
            _sv = session_getter(session_id)
            if _sv and _sv.score and hasattr(_sv.score, 'latest_version'):
                _version = _sv.score.latest_version()
        except Exception:
            pass
        if new_abc:
            await publish("abc.updated", {
                "abc":     new_abc,
                "version": _version,
                "summary": summary,
                "meta": {
                    "title":       new_header.get("title") or getattr(meta, "title", ""),
                    "key":         new_header.get("key", "C"),
                    "bpm":         new_header.get("bpm", 120),
                    "note_count":  count_notes(new_abc),
                    "time_sig":    {
                        "num": new_header.get("time_sig_num", 4),
                        "den": new_header.get("time_sig_den", 4),
                    },
                    "pitch_level": getattr(meta, "pitch_level", 0),
                    "composer":    getattr(meta, "composer", ""),
                },
            })

        reply = f"✅ {summary}"
        await stream_text(reply, publish)
        await assert_finish_gate(todo_mgr, "edit", publish)
        await publish("message.completed", {"message": reply})
        return {
            "domain":       "edit",
            "message":      reply,
            "abc_updated":  bool(new_abc),
            "abc_notation": new_abc,
            "summary":      summary,
            **result,
        }

    async def run(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。
        AGENT-2 修复：session_getter/saver 通过 ctx 属性统一解包（fallback 逻辑在 RunContext 中）。
        BUG-CH1 修复：链式 convert→edit 时，chain_ctx 携带了 current_abc；
                      若 session_saver 在 convert 阶段静默失败，session 中可能仍是旧谱子。
                      此处将 current_abc 补写入 session，确保 EditAgent.run() 读到正确谱子。
        """
        edit_fn  = ctx.extra.get("edit_fn") or (lambda *a, **kw: None)
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id

        # BUG-CH1 修复：链式 convert→edit 传入的 current_abc 补写 session
        _current_abc = ctx.extra.get("current_abc", "")
        if _current_abc:
            try:
                _sess = ctx.session_getter(ctx.session_id)
                if _sess and (not _sess.score or not _sess.score.abc_notation):
                    from app.agentcore.abc_utils import parse_abc_header, count_notes
                    from app.pipeline.domain import Score, ScoreMeta
                    _h = parse_abc_header(_current_abc)
                    _sess.score = Score(
                        title=_h["title"] or "score",
                        abc_notation=_current_abc,
                        meta=ScoreMeta(
                            title=_h["title"] or "score",
                            key=_h["key"],
                            bpm=float(_h["bpm"] or 120),
                            note_count=count_notes(_current_abc),
                            time_sig_num=_h.get("time_sig_num", 4),
                            time_sig_den=_h.get("time_sig_den", 4),
                        ),
                    )
                    ctx.session_saver(_sess)
            except Exception:
                pass

        return await self._run_impl(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            edit_fn=edit_fn,
            todo_mgr=todo_mgr,
            session_getter=ctx.session_getter,
            session_saver=ctx.session_saver,
            workspace_id=ctx.workspace_id,
        )
