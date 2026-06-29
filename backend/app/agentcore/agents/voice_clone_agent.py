"""
VoiceCloneAgent — GPT-SoVITS 音色克隆专家 SubAgent（v1.0）

职责（单一）：
  - 专门处理 sovits 域的音色克隆和语音合成请求
  - 优先使用 GPT-SoVITS（本地部署，免费无限制）
  - 降级策略：GPT-SoVITS 未配置时自动切换 MiniMax 音色克隆
  - 统一管理 sovits 域 TODO 状态
  - 支持：零样本克隆 / TTS / 模型切换 / 音频保存

工作流：
  1. 检查 GPT-SoVITS 服务是否在线
  2. 在线 → ReactExecutor + sovits 工具组
  3. 离线 → ReactExecutor + audio 工具组（MiniMax 降级）
  4. 结果保存到工作区 audio/ 目录
"""
from __future__ import annotations

from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text, ReactExecutor

Publisher = Callable[[str, dict], Awaitable[None]]


# ── voice-clone-expert.agent 文件路径（热加载）──────────────────────────────
_AGENT_FILE = "voice-clone-expert"


@register("sovits")
class VoiceCloneAgent:
    """
    GPT-SoVITS 音色克隆专家 SubAgent。

    执行流程：
      1. tick TODO[0] → running
      2. 检查 GPT-SoVITS 服务状态
      3. 在线 → sovits 工具组 ReAct Loop
         离线 → audio 工具组 ReAct Loop（MiniMax 降级）
      4. complete_one TODO[0] → done
      5. 保存音频到工作区（若有 workspace_id）
      6. finish_all + assert_finish_gate
    """

    async def run(
        self,
        session_id: str,
        message: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        workspace_id: str = "",
        attachment_b64: str = "",
        attachment_name: str = "",
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        # ── 检查 GPT-SoVITS 服务 ──────────────────────────────────────
        sovits_online = await self._check_sovits_online()

        provider = "sovits" if sovits_online else "minimax"
        # v2.0：sovits 工具已内置自动落盘，无需挂载 workspace 工具组
        # 降级时只取 audio 组中 minimax 音色克隆相关的 5 个工具，避免混入 suno/lyrics 等无关工具
        # finish_task 通过 get_tool_schemas() 全量兜底获取（不依赖特定组）
        tool_groups = ["sovits"] if sovits_online else ["audio"]
        # 降级时的工具白名单（audio 组有 12 个工具，只需其中 5 个）
        _minimax_whitelist = {
            "upload_voice_sample", "upload_prompt_audio",
            "clone_voice_minimax", "list_cloned_voices", "synthesize_speech_minimax",
        }

        await publish("tool.call", {
            "call_id":   f"call_voice_{session_id[:8]}",
            "tool":      "voice_clone_router",
            "status":    "running",
            "arguments": {
                "provider":   provider,
                "has_audio":  bool(attachment_b64),
                "message":    message[:80],
            },
        })

        if not sovits_online:
            await stream_text(
                "⚠️ GPT-SoVITS 服务未启动，已自动切换到 MiniMax 音色克隆。\n"
                "如需使用本地免费克隆，请运行 `EP-Agent/sovits-installer/start.sh` 启动服务。",
                publish,
            )

        try:
            result = await self._run_react(
                message=message,
                publish=publish,
                todo_mgr=todo_mgr,
                session_id=session_id,
                workspace_id=workspace_id,
                attachment_b64=attachment_b64,
                attachment_name=attachment_name,
                tool_groups=tool_groups,
                provider=provider,
            )
        except Exception as e:
            await publish("tool.call", {
                "call_id": f"call_voice_{session_id[:8]}",
                "tool":    "voice_clone_router",
                "status":  "failed",
                "error":   str(e),
            })
            await todo_mgr.finish_all(publish, "failed")
            await assert_finish_gate(todo_mgr, "sovits", publish)
            reply = f"音色克隆失败：{e}"
            await stream_text(reply, publish)
            await publish("message.completed", {"message": reply})
            return {"domain": "sovits", "message": reply}

        summary = result.get("content") or result.get("summary") or "音色克隆完成"

        await publish("tool.call", {
            "call_id":        f"call_voice_{session_id[:8]}",
            "tool":           "voice_clone_router",
            "status":         "succeeded",
            "result_preview": summary[:120],
        })

        if ids:
            await todo_mgr.complete_one(ids[0], publish)
        await todo_mgr.finish_all(publish, "done")
        await assert_finish_gate(todo_mgr, "sovits", publish)

        await stream_text(summary, publish)
        await publish("message.completed", {"message": summary})
        return {
            "domain":      "sovits",
            "message":     summary,
            "provider":    provider,
            "abc_updated": False,
            **result.get("extra", {}),
        }

    # ── 内部方法 ──────────────────────────────────────────────────────

    async def _check_sovits_online(self) -> bool:
        """快速检查 GPT-SoVITS 服务是否在线（超时 3 秒）。"""
        try:
            from app.config import config as _cfg
            base_url = getattr(_cfg, "SOVITS_BASE_URL", "")
            if not base_url:
                return False
            import httpx
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(base_url.rstrip("/") + "/")
                return resp.status_code < 500
        except Exception:
            return False

    async def _run_react(
        self,
        message: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        session_id: str,
        workspace_id: str,
        attachment_b64: str,
        attachment_name: str,
        tool_groups: list[str],
        provider: str,
    ) -> dict:
        """使用 ReactExecutor 执行音色克隆 ReAct Loop。"""
        from app.agentcore.tools import get_tool_schemas, call_tool
        from app.agentcore.agent_loader import load_agent_prompt

        # 加载 voice-clone-expert.agent 的 system prompt
        try:
            system_prompt = load_agent_prompt(_AGENT_FILE)
        except Exception:
            system_prompt = _FALLBACK_SYSTEM

        # 收集工具 schema
        # 降级路径（audio 组）只取 minimax 音色克隆相关工具，过滤掉 suno/lyrics 等无关工具
        tools = []
        for g in tool_groups:
            schemas = get_tool_schemas(g)
            if g == "audio":
                schemas = [s for s in schemas if s["function"]["name"] in _minimax_whitelist]
            tools.extend(schemas)
        # finish_task 兜底：sovits/audio 组均不含 finish_task，从全量工具中补充
        _ft = [t for t in get_tool_schemas() if t["function"]["name"] == "finish_task"]
        if _ft and not any(t["function"]["name"] == "finish_task" for t in tools):
            tools.extend(_ft[:1])

        # 构造 user prompt
        # v2.0 铁律：b64 绝不注入 messages，防止上下文爆炸。
        # 附件在此处落盘，LLM 只感知 workspace_path 字符串。
        user_parts = [message]
        if attachment_b64 and attachment_name:
            ref_ws_path = await self._save_attachment(attachment_b64, attachment_name)
            if ref_ws_path:
                user_parts.append(
                    f"\n[参考音频已保存: {ref_ws_path}，"
                    f"请使用 ref_audio_workspace_path=\"{ref_ws_path}\" 参数]"
                )
            else:
                user_parts.append(
                    f"\n[参考音频附件: {attachment_name}，落盘失败，请让用户重新上传]"
                )

        user_message = "".join(user_parts)

        executor = ReactExecutor()
        result = await executor.run(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            tools=tools,
            publish=publish,
            todo_manager=todo_mgr,
            max_rounds=5,
            session_id=session_id,
        )

        return result

    async def _save_attachment(self, attachment_b64: str, attachment_name: str) -> str:
        """
        将 b64 附件落盘到项目 audio/ 目录，返回 workspace_path。
        b64 数据在此处处理完毕，绝不上浮到 LLM 消息层。
        """
        import base64
        from app.agentcore.tools.sovits_tools import _save_audio_bytes
        try:
            raw = attachment_b64
            if raw.startswith("data:") and ";base64," in raw:
                raw = raw.split(";base64,", 1)[1]
            audio_bytes = base64.b64decode(raw)
            ext = attachment_name.rsplit(".", 1)[-1].lower() if "." in attachment_name else "wav"
            stem = attachment_name.rsplit(".", 1)[0]
            result = _save_audio_bytes(audio_bytes, f"ref_{stem}", ext)
            return result.get("workspace_path", "")
        except Exception as e:
            import logging
            logging.getLogger("ep_agent.sovits").warning("附件落盘失败: %s", e)
            return ""

    async def run_with_ctx(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。"""
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self.run(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            todo_mgr=todo_mgr,
            workspace_id=ctx.workspace_id,
            attachment_b64=ctx.attachment_b64,
            attachment_name=ctx.attachment_name,
        )


# ── 降级 system prompt（agent 文件不存在时使用）────────────────────────────
_FALLBACK_SYSTEM = """你是 EP-Agent 的音色克隆专家，专注于 GPT-SoVITS 语音合成。

工具使用顺序（v2.0）：
1. sovits_health_check     — 先检查服务状态
2. sovits_list_models      — 查看可用模型（可选）
3. sovits_tts_and_save     — 文字转语音 + 自动保存（一步完成）
4. sovits_clone_and_save   — 零样本克隆 + 自动保存（一步完成）
5. sovits_list_audio_files — 列出已保存音频（可选确认）
6. finish_task             — 完成任务

铁律（v2.0）：
- 绝对禁止在任何工具参数中传递 base64 或二进制数据，防止上下文爆炸
- 参考音频通过 ref_audio_workspace_path 传递工作区路径（如 audio/ref.wav）
- 合成后自动落盘，无需单独调用保存工具
- 最终必须调用 finish_task
"""

