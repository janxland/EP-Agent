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

from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.react_executor import stream_text

Publisher = Callable[[str, dict], Awaitable[None]]


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
            from app.agentcore.abc_utils import parse_abc_header, count_notes

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

        # 落库由 service.universal_chat 在 SubAgent 返回后统一执行（避免双写）
        # SubAgent 只操作内存 session（session_saver），不直接调用 db 层

        await publish("abc.updated", {
            "abc":     new_abc,
            "version": 2,
            "summary": summary,
            "meta": {
                "title":      getattr(meta, "title", ""),
                "key":        getattr(meta, "key", "C"),
                "bpm":        getattr(meta, "bpm", 120.0),
                "note_count": getattr(meta, "note_count", 0),
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
