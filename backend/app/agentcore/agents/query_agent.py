"""
QueryAgent — 谱子查询/问答 SubAgent

职责（单一）：
  - 注入谱子上下文 + 对话历史
  - 流式 LLM 回答用户问题
  - 管理 query 域 TODO 状态（LLM 真实回答后才 complete_one）
  - 异常路径：finish_all(failed) + assert_finish_gate + message.completed
"""
from __future__ import annotations

from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.react_executor import stream_llm, stream_text

Publisher = Callable[[str, dict], Awaitable[None]]


class QueryAgent:
    """谱子查询/问答 SubAgent，LLM 直接流式回答。"""

    async def run(
        self,
        session_id: str,
        message: str,
        publish: Publisher,
        session_getter: Callable,
        todo_mgr: TodoManager,
        role_id: str | None = None,   # ← 角色 ID，注入角色专属 system prompt
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        sess = session_getter(session_id)

        # ── 角色专属 system prompt 注入 ──────────────────────────────────────
        from app.agentcore.role_config import get_role_or_default
        role = get_role_or_default(role_id)
        base_prompt = (
            f"{role.system_prompt_extra}\n\n"
            "用中文简洁回答用户问题。"
        )
        system_parts = [base_prompt]

        # 注入谱子上下文
        if sess and sess.score:
            m = sess.score.meta
            system_parts.append(
                f"当前谱子：《{m.title}》，调号={m.key}，BPM={m.bpm:.0f}，"
                f"拍号={m.time_sig_num}/{m.time_sig_den}，音符数={m.note_count}\n"
                f"ABC 谱（前800字）：\n{sess.score.abc_notation[:800]}"
            )

        # 注入对话历史（最近 3 条）
        if sess and sess.intent_history:
            lines = [f"- {r.intent_type}：{r.summary}" for r in sess.intent_history[-3:]]
            system_parts.append("历史操作记录：\n" + "\n".join(lines))

        # ── LLM 真实回答（流式输出）─────────────────────────────────────────
        try:
            answer = await stream_llm([
                {"role": "system", "content": "\n\n".join(system_parts)},
                {"role": "user",   "content": message},
            ], publish)
        except Exception as e:
            # 异常路径：finish_all(failed) → gate → message.completed
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "query", publish)
            reply = f"回答失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "query", "message": reply, "abc_updated": False}

        # ── 成功路径：LLM 真实回答后才 complete_one（v3 纪律）────────────────
        if ids:
            await todo_mgr.complete_one(ids[0], publish)

        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, "query", publish)
        await publish("message.completed", {"message": answer})
        return {"domain": "query", "message": answer, "abc_updated": False}
