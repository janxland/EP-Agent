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
from app.agentcore.react_executor import stream_text
from app.agentcore.abc_utils import parse_abc_header, count_notes
from app.agentcore.agent_registry import register

if TYPE_CHECKING:
    from app.agentcore.run_context import RunContext

Publisher = Callable[[str, dict], Awaitable[None]]


@register("edit")
class EditAgent:
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

    async def run(
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
        sess = session_getter(session_id)
        if not sess or not sess.score:
            reply = "当前没有谱子可以编辑，请先上传或创作一首谱子。"
            await stream_text(reply, publish)
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "edit", publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "edit", "message": reply, "abc_updated": False}

        current_abc = sess.score.abc_notation
        meta        = sess.score.meta

        # 注入历史上下文（让 LLM 感知上次做了什么）
        context_summary = ""
        if sess.intent_history:
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
        # workspace_id 不再注入 prompt（abc_to_midi 等工具通过 ContextVar 自动推断路径）
        try:
            from app.agentcore.edit_runner import run_edit
            result = await run_edit(
                current_abc=current_abc,
                intent=message,
                meta=meta,
                context_summary=context_summary,
                publish=publish,
                todo_mgr=todo_mgr,
                scene="editor",
                session_id=session_id,  # 落库 tool message
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

        new_abc = result.get("abc", current_abc)
        summary = result.get("summary", "修改完成")

        await publish("tool.call", {
            "call_id":        edit_call_id,
            "tool":           "abc_editor",
            "status":         "succeeded",
            "result_preview": summary,
        })

        # ── TODO 纪律：真实落地后 complete_one ───────────────────────────────
        # ReactExecutor 在 ReAct Loop 内部已经 complete_one 了 running TODO，
        # 此处只需 finish_all 收尾剩余 pending TODO（如"验证结果"等）
        await todo_mgr.finish_all(publish, "done")

        # ── 落库 + 推送 abc.updated ───────────────────────────────────────────
        try:
            from app.pipeline.domain import Score, ScoreMeta

            header = parse_abc_header(new_abc)
            new_meta = ScoreMeta(
                title      = header["title"],
                key        = header["key"],
                bpm        = header["bpm"],
                note_count = count_notes(new_abc),
            )
            new_score = Score(
                title        = header["title"],
                abc_notation = new_abc,
                meta         = new_meta,
            )
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
        new_header = parse_abc_header(new_abc)
        await publish("abc.updated", {
            "abc":     new_abc,
            "version": (getattr(getattr(session_getter(session_id), 'score', None), 'latest_version', lambda: 2)() if session_getter else 2),
            "summary": summary,
            "meta": {
                "title":       new_header["title"] or getattr(meta, "title", ""),
                "key":         new_header["key"],          # ← 转调后新调号
                "bpm":         new_header["bpm"],          # ← 变速后新 BPM
                "note_count":  count_notes(new_abc),       # ← Body 音符数（已修复）
                "time_sig":    {
                    "num": new_header["time_sig_num"],
                    "den": new_header["time_sig_den"],
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
            "abc_updated":  True,
            "abc_notation": new_abc,
            "summary":      summary,
            **result,
        }

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。"""
        from app.pipeline import db as _db
        session_getter = ctx.extra.get("session_getter") or _db.get_session
        session_saver  = ctx.extra.get("session_saver")  or _db.save_session
        edit_fn        = ctx.extra.get("edit_fn") or (lambda *a, **kw: None)
        todo_mgr       = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            edit_fn=edit_fn,
            todo_mgr=todo_mgr,
            session_getter=session_getter,
            session_saver=session_saver,
            workspace_id=ctx.workspace_id,
        )
