"""
AudioAgent — 音频/音色 SubAgent

职责（单一）：
  - 委托 audio_chat_fn 执行音频生成/迭代/翻唱/音色克隆
  - voice 域优先走 GPT-SoVITS（若已配置 SOVITS_BASE_URL）
  - 统一管理 audio/voice 域 TODO 状态
  - 异常路径 finish_all(failed) + assert_finish_gate

修复的架构问题：
  原 audio_runner 有独立 ReAct Loop，绕过了 TodoManager 和 finish_gate。
  现在 AudioAgent 在外层统一管理 TODO，audio_runner 只负责音频生成逻辑。
"""
from __future__ import annotations

from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text, ReactExecutor

Publisher = Callable[[str, dict], Awaitable[None]]



@register("audio", "voice")
class AudioAgent:
    """音频/音色 SubAgent，委托 audio_chat_fn 或 SoVITS ReactExecutor 执行。"""

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_b64: str,
        publish: Publisher,
        audio_chat_fn: Callable,
        todo_mgr: TodoManager,
        domain: str,
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        # audio/voice 域：均走 audio_chat_fn（MiniMax/Suno）
        # 音色克隆（sovits 域）由 VoiceCloneAgent 专门处理，不在此分支
        tool_name = "audio_generator" if domain == "audio" else "voice_clone"

        audio_call_id = f"call_audio_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   audio_call_id,
            "tool":      tool_name,
            "status":    "running",
            "arguments": {"message": message[:80], "has_audio": bool(attachment_b64)},
        })

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
            return {"domain": domain, "message": reply, "abc_updated": False}

        r       = result if isinstance(result, dict) else {}
        summary = r.get("summary", "音频已生成")

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
        return {"domain": domain, "message": summary, "abc_updated": False, **r}

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。"""
        audio_chat_fn = ctx.extra.get("audio_chat_fn") or (lambda *a, **kw: {})
        todo_mgr      = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            attachment_b64=ctx.attachment_b64,
            publish=ctx.publish,
            audio_chat_fn=audio_chat_fn,
            todo_mgr=todo_mgr,
            domain=ctx.domain or "audio",
        )

