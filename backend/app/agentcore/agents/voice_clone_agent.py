"""
VoiceCloneAgent — GPT-SoVITS 音色克隆专家 SubAgent（v2.0）

设计原则（低耦合高内聚）：
  - 文件就是文件：Agent 只感知 workspace_path，永远不接触 base64
  - 前端负责把文件上传到工作区，Agent 只管用路径
  - 单一职责：专门处理 sovits 域的音色克隆和语音合成请求
  - 优先使用 GPT-SoVITS（本地部署，免费无限制）
  - 降级策略：GPT-SoVITS 未配置时自动切换 MiniMax 音色克隆

工作流：
  1. 检查 GPT-SoVITS 服务是否在线
  2. 在线 → ReactExecutor + sovits 工具组
  3. 离线 → ReactExecutor + audio 工具组（MiniMax 降级）
  4. 结果保存到工作区 audio/ 目录
"""
from __future__ import annotations

from typing import Callable, Awaitable

from app.agentcore.todo_manager import TodoManager, assert_finish_gate
from app.agentcore.agents.base_agent import BaseAgent
from app.agentcore.agent_registry import register

if False:  # TYPE_CHECKING
    from app.agentcore.run_context import RunContext
from app.agentcore.react_executor import stream_text, ReactExecutor

Publisher = Callable[[str, dict], Awaitable[None]]


# ── voice-clone-expert.agent 文件路径（热加载）──────────────────────────────
_AGENT_FILE = "voice-clone-expert"

# MiniMax 降级时的工具白名单（audio 组有 12 个工具，只需音色克隆相关的 5 个）
_MINIMAX_TOOLS = {
    "upload_voice_sample", "upload_prompt_audio",
    "clone_voice_minimax", "list_cloned_voices", "synthesize_speech_minimax",
}


@register("sovits")
class VoiceCloneAgent(BaseAgent):
    """
    GPT-SoVITS 音色克隆专家 SubAgent。

    执行流程：
      1. tick TODO[0] → running
      2. 检查 GPT-SoVITS 服务状态
      3. 在线 → sovits 工具组 ReAct Loop
         离线 → audio 工具组 ReAct Loop（MiniMax 降级）
      4. complete_one TODO[0] → done
      5. finish_all + assert_finish_gate
    """

    async def _run_impl(
        self,
        session_id: str,
        message: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        workspace_id: str = "",
        attachment_name: str = "",
        attachment_workspace_path: str = "",  # 文件已在工作区的路径，唯一附件传递方式
    ) -> dict:
        ids = todo_mgr.get_ids()
        if ids:
            await todo_mgr.tick(ids[0], "running", publish)

        # ── 检查 GPT-SoVITS 服务 ──────────────────────────────────────
        sovits_online = await self._check_sovits_online()

        provider = "sovits" if sovits_online else "minimax"
        tool_groups = ["sovits"] if sovits_online else ["audio"]

        await publish("tool.call", {
            "call_id":   f"call_voice_{session_id[:8]}",
            "tool":      "voice_clone_router",
            "status":    "running",
            "arguments": {
                "provider":   provider,
                "has_audio":  bool(attachment_workspace_path),
                "audio_path": attachment_workspace_path or "none",
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
                attachment_workspace_path=attachment_workspace_path,
                tool_groups=tool_groups,
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
            "_persisted":  result.get("_persisted", False),  # 透传 ReactExecutor 落库标记，防 service.py 重复写入
            **result.get("extra", {}),
        }

    # ── 内部方法 ──────────────────────────────────────────────────────

    async def _check_sovits_online(self) -> bool:
        """快速检查 GPT-SoVITS 服务是否在线（超时 3 秒）。
        用 POST /tts 发不完整参数探测：返回 4xx = 服务在线（参数错误但服务正常），
        5xx 或连接失败 = 服务异常。不能用 GET / 因为 GPT-SoVITS 返回 404。
        """
        try:
            from app.config import config as _cfg
            import os as _os
            base_url = _os.getenv("SOVITS_BASE_URL") or getattr(_cfg, "SOVITS_BASE_URL", "")
            if not base_url:
                return False
            import httpx
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(
                    base_url.rstrip("/") + "/tts",
                    json={"text": "ping", "text_lang": "zh"},
                    headers={"Content-Type": "application/json"},
                )
                # 2xx / 4xx 均视为在线（4xx = 参数不全但服务正常）
                return resp.status_code < 500
        except Exception:
            return False

    async def _run_react(
        self,
        message: str,
        publish: Publisher,
        todo_mgr: TodoManager,
        session_id: str,
        attachment_workspace_path: str,
        tool_groups: list[str],
    ) -> dict:
        """使用 ReactExecutor 执行音色克隆 ReAct Loop。"""
        from app.agentcore.tools import get_tool_schemas
        from app.agentcore.agent_loader import load_agent_prompt

        # 加载 voice-clone-expert.agent 的 system prompt
        try:
            system_prompt = load_agent_prompt(_AGENT_FILE)
        except Exception:
            system_prompt = _FALLBACK_SYSTEM

        # 收集工具 schema（MiniMax 降级时只取白名单内的 5 个工具）
        tools = []
        for g in tool_groups:
            schemas = get_tool_schemas(g)
            if g == "audio":
                schemas = [s for s in schemas if s["function"]["name"] in _MINIMAX_TOOLS]
            tools.extend(schemas)
        # finish_task 兜底
        _ft = [t for t in get_tool_schemas() if t["function"]["name"] == "finish_task"]
        if _ft and not any(t["function"]["name"] == "finish_task" for t in tools):
            tools.extend(_ft[:1])

        # 构造 user prompt：LLM 只感知 workspace_path 字符串，永不接触二进制
        user_parts = [message]
        if attachment_workspace_path:
            user_parts.append(
                f"\n[参考音频已在工作区: {attachment_workspace_path}，"
                f"请使用 ref_audio_workspace_path=\"{attachment_workspace_path}\" 参数]"
            )

        executor = ReactExecutor()
        return await executor.run(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": "".join(user_parts)},
            ],
            tools=tools,
            publish=publish,
            todo_manager=todo_mgr,
            max_rounds=5,
            session_id=session_id,
        )

    async def run(self, ctx: "RunContext") -> dict:
        """v4.0 解耦接口：从 RunContext 解包参数，调用原 run()。"""
        todo_mgr = ctx.extra.get("todo_mgr")
        if todo_mgr is None:
            from app.agentcore.todo_manager import TodoManager as _TM
            todo_mgr = _TM()
            todo_mgr.session_id = ctx.session_id
        return await self._run_impl(
            session_id=ctx.session_id,
            message=ctx.message,
            publish=ctx.publish,
            todo_mgr=todo_mgr,
            workspace_id=ctx.workspace_id,
            attachment_name=ctx.attachment_name,
            attachment_workspace_path=ctx.attachment_workspace_path,
        )


# ── 降级 system prompt（agent 文件不存在时使用）────────────────────────────
_FALLBACK_SYSTEM = """你是 EP-Agent 的音色克隆专家，专注于 GPT-SoVITS 语音合成。

铁律（v3.0）：
1. 系统已在调用前确认服务状态，**直接执行任务，无需先调用** sovits_health_check
2. **路径必须先查**：无论用户消息是否有胶囊，都先调 sovits_list_audio_files() 获取真实 workspace_path
   - 原因：用户消息的 [@xxx] 胶囊路径可能是旧路径，不可直接使用
   - sovits_list_audio_files() 返回的 workspace_path 才是当前真实路径
3. **GPT-SoVITS 强制要求参考音频**：必须提供 ref_audio_workspace_path，否则报错
4. ref_audio_workspace_path 必须使用 sovits_list_audio_files() 返回的 workspace_path 字段原值
   - ✅ 正确示例：ref_audio_workspace_path="vo_furina.wav"（list 返回的原值）
   - ❌ 错误示例：ref_audio_workspace_path="shared/vo_furina.wav"（不要加任何前缀）
5. 合成后自动落盘，无需单独调用保存工具
6. 最终必须调用 finish_task

工具使用顺序（v3.0，必须严格遵守）：
1. sovits_list_audio_files()  ← 【必须第一步】获取项目内真实音频路径列表
2. sovits_clone_and_save(target_text=..., ref_audio_workspace_path=<list返回的workspace_path>)
3. finish_task(summary=...)

路径规则（重要）：
- ref_audio_workspace_path 只接受相对于项目根目录的路径
- 系统内部会自动将相对路径转换为 GPT-SoVITS 所需的服务器绝对路径
- 你只需传 workspace_path 字段的原值，不要拼接任何前缀或绝对路径
"""
