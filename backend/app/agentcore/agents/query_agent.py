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
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_llm, stream_text

Publisher = Callable[[str, dict], Awaitable[None]]


@register("query")
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

        # ── 注入工作区文件记忆（让 QueryAgent 能回答「我的文件在哪里」类问题）──
        try:
            from app.agentcore.session_context import is_context_set, ctx_get_session
            if is_context_set():
                _sess_mem = ctx_get_session(session_id)
                if _sess_mem:
                    _extra = _sess_mem.extra if isinstance(_sess_mem.extra, dict) else {}
                    _ws_files = _extra.get("workspace_files", {})
                    _memory   = _extra.get("memory", {})
                    _mem_lines = []
                    # workspace_files：规则写入的文件路径
                    for ftype, flist in _ws_files.items():
                        for f in flist[:3]:  # 每类最多 3 条，防 context 膨胀
                            _mem_lines.append(f"  - [{ftype}] {f['path']}")
                    # memory.key_files：LLM 压缩后的高价值文件
                    for f in _memory.get("key_files", [])[:5]:
                        _path = f.get('path', '')
                        if _path and not any(_path in l for l in _mem_lines):
                            _mem_lines.append(f"  - [{f.get('type','?')}] {_path}")
                    if _mem_lines:
                        system_parts.append(
                            "工作区已知文件（可直接引用路径回答用户）：\n" + "\n".join(_mem_lines)
                        )
                    # memory.summary：对话摘要
                    if _memory.get("summary"):
                        system_parts.append(f"历史摘要：{_memory['summary']}")
        except Exception:
            pass

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

        # ── 注入重要记忆前缀（用户意图、已有文件等跨轮次携带体）─────────────
        try:
            from app.agentcore.memory_manager import build_memory_prefix
            _mem = build_memory_prefix(session_id)
            if _mem:
                system_parts.append(_mem)
        except Exception:
            pass

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

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。
        AGENT-2 修复：session_getter 通过 ctx 属性统一解包（fallback 逻辑在 RunContext 中）。
        """
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            session_getter=ctx.session_getter,
            todo_mgr=todo_mgr,
            role_id=ctx.role_id or None,
        )

