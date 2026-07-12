"""
AudioAgent — 音频/音色 SubAgent (v5.1 LangGraph 兼容)

职责（单一）：
  - 委托 audio_chat_fn 执行音频生成/迭代/翻唱/音色克隆
  - voice 域优先走 GPT-SoVITS（若已配置 SOVITS_BASE_URL）
  - 统一管理 audio/voice 域 TODO 状态
  - 异常路径 finish_all(failed) + assert_finish_gate

v5.1 修改：
  - 继承 BaseAgent（统一 run(ctx) 接口，纳入图引擎节点工厂体系）
  - run(ctx) 从 RunContext 解包参数，替代原散参数 run()
  - run_with_ctx() 继承 BaseAgent 默认实现，无需重写
  - 保留原 audio_chat_fn 缺失时的明确报错（AGENT-5 修复）
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.agentcore.agents.base_agent import BaseAgent
from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register
from app.pipeline import db as _db

if TYPE_CHECKING:
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text


@register("audio", "voice")
class AudioAgent(BaseAgent):
    """音频/音色 SubAgent，委托 audio_chat_fn 或 SoVITS ReactExecutor 执行。

    继承 BaseAgent，纳入图引擎节点工厂（_make_node）体系：
      - graph_engine 通过 AgentCls().run_with_ctx(ctx) 调用
      - BaseAgent.run_with_ctx() → BaseAgent.execute() → self.run(ctx)
    """

    async def run(self, ctx: "RunContext") -> dict:
        """
        v5.1 统一接口：从 RunContext 解包所有参数。

        ctx.extra 必须包含：
          - audio_chat_fn : 音频生成回调（由 _dispatch_v5 / _dispatch 注入）
          - todo_mgr      : TodoManager 实例（BaseAgent.run_with_ctx 自动注入）
        """
        # ── 解包参数 ────────────────────────────────────────────────────────
        audio_chat_fn = ctx.extra.get("audio_chat_fn")
        if audio_chat_fn is None:
            import logging as _log
            _log.getLogger("ep_agent.audio_agent").error(
                "[AudioAgent] audio_chat_fn 未注入，请确认 _dispatch_v5 传入 audio_chat_fn"
            )
            async def _missing_audio_fn(**kw):
                raise RuntimeError("audio_chat_fn 未配置，请联系管理员配置音频服务")
            audio_chat_fn = _missing_audio_fn

        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            todo_mgr = TodoManager()
            todo_mgr.session_id = ctx.session_id

        session_id    = ctx.session_id
        message       = ctx.message
        attachment_b64 = ctx.attachment_b64
        publish       = ctx.publish
        domain        = ctx.domain or "audio"

        # ── TODO 状态推进 ───────────────────────────────────────────────────
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        tool_name = "audio_generator" if domain == "audio" else "voice_clone"

        audio_call_id = f"call_audio_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   audio_call_id,
            "tool":      tool_name,
            "status":    "running",
            "arguments": {"message": message[:80], "has_audio": bool(attachment_b64)},
        })

        # ── 调用音频生成 ────────────────────────────────────────────────────
        try:
            result = await audio_chat_fn(
                session_id=session_id,
                message=message,
                provider="auto",
                audio_b64=attachment_b64,
                publish=publish,
            )
        except Exception as e:
            await publish("tool.call", {
                "call_id": audio_call_id, "tool": tool_name,
                "status": "failed", "error": str(e),
            })
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, domain, publish)
            reply = f"音频/语音生成失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            # 落库：失败时 assistant 回复消息
            if session_id:
                try:
                    _db.insert_message(
                        msg_id=f"asst_{uuid.uuid4().hex[:12]}",
                        session_id=session_id,
                        role="assistant",
                        content=reply,
                    )
                except Exception:
                    pass
            return {"domain": domain, "message": reply, "abc_updated": False, "_persisted": True}

        r       = result if isinstance(result, dict) else {}
        summary = r.get("summary", "音频已生成")

        # 注意：audio_runner 已在 Tool-Calling Loop 内部逐条落库 assistant/tool 消息，
        # 此处不再重复落库，避免消息重复写入。

        await publish("tool.call", {
            "call_id":        audio_call_id,
            "tool":           tool_name,
            "status":         "succeeded",
            "result_preview": summary,
        })

        if ids:
            await todo_mgr.complete_one(ids[0], publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, domain, publish)

        await stream_text(summary, publish)
        await publish("message.completed", {"message": summary})

        # 落库：最终 assistant 文字回复
        if session_id:
            try:
                _db.insert_message(
                    msg_id=f"asst_{uuid.uuid4().hex[:12]}",
                    session_id=session_id,
                    role="assistant",
                    content=summary,
                )
            except Exception:
                pass

        # _persisted=True 告知 service.universal_chat 不再重复落库
        return {"domain": domain, "message": summary, "abc_updated": False, "_persisted": True, **r}
