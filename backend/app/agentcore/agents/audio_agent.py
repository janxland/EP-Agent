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
from app.agentcore.react_executor import stream_text, ReactExecutor

Publisher = Callable[[str, dict], Awaitable[None]]

# 意图域 → 工具组映射（与 universal_runner 保持同步）
_DOMAIN_TOOL_GROUPS: dict[str, list[str]] = {
    "audio":   ["audio"],
    "voice":   ["audio"],
    "sovits":  ["sovits"],
}


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

        # voice 域：优先走 sovits（若已配置）
        use_sovits = self._should_use_sovits(domain)
        tool_name  = (
            "voice_clone_sovits" if use_sovits
            else ("audio_generator" if domain == "audio" else "voice_clone")
        )

        audio_call_id = f"call_audio_{session_id[:8]}"
        await publish("tool.call", {
            "call_id":   audio_call_id,
            "tool":      tool_name,
            "status":    "running",
            "arguments": {"message": message[:80], "has_audio": bool(attachment_b64)},
        })

        try:
            if use_sovits:
                result = await self._run_sovits(message, publish, todo_mgr, session_id=session_id)
            else:
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

    def _should_use_sovits(self, domain: str) -> bool:
        if domain != "voice":
            return False
        try:
            from app.config import config as _cfg
            return bool(getattr(_cfg, "SOVITS_BASE_URL", ""))
        except Exception:
            return False

    async def _run_sovits(
        self,
        message: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        session_id: str = "",
    ) -> dict:
        """使用 ReactExecutor + sovits 工具组执行语音合成。"""
        from app.agentcore.tools import get_tool_schemas
        sovits_tools = get_tool_schemas("sovits")
        executor     = ReactExecutor()
        exec_result  = await executor.run(
            messages=[
                {"role": "system", "content": (
                    "你是 EP-Agent 的语音助手，负责音色克隆和语音合成。"
                    "根据用户需求选择合适的工具："
                    "sovits_tts（文字转语音）、"
                    "sovits_clone_voice（克隆音色）、"
                    "sovits_list_models（查看可用模型）。"
                )},
                {"role": "user", "content": message},
            ],
            tools=sovits_tools,
            publish=publish,
            todo_manager=todo_mgr,
            max_rounds=3,
            session_id=session_id,  # 落库 tool message
        )
        return {
            "summary":  exec_result.get("content") or "语音合成完成",
            "provider": "sovits",
            **exec_result.get("extra", {}),
        }
