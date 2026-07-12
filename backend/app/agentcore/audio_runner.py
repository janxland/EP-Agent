"""
Audio Chat Agent Runner
对话式音频生成：支持首次生成 + 迭代改进（"再欢快一点"式交互）

流程：
  用户消息
    → Router LLM（识别意图域：generate / iterate / cover）
    → 按意图加载对应工具子集（工具发现）
    → Audio Agent LLM（携带 audio 分组工具）
    → 工具执行（生成/进化）
    → 保存本次参数到 AudioSession（供下次迭代）
    → 返回结果 + 建议

AudioSession 结构（存于 Session.audio_history）：
  [
    {
      "turn": 1,
      "user_message": "给这首谱子配乐",
      "prompt": "upbeat, chinese traditional, guzheng",
      "style": "chinese traditional, guzheng",
      "lyrics": "",
      "instrumental": true,
      "provider": "minimax",
      "model": "music-2.6",
      "audio_url": "https://...",
      "duration_ms": 25000,
      "summary": "生成了一段中国风纯音乐",
      "suggestions": ["可以加入人声", "试试爵士风格"]
    },
    ...
  ]
"""
from __future__ import annotations
import asyncio
import json
from typing import Callable, Awaitable

from app.agentcore.llm import complete, complete_with_tools
from app.pipeline.domain import ScoreMeta
from app.agentcore.tools import get_tool_schemas, call_tool
from app.agentcore.service_health import service_health

Publisher = Callable[[str, dict], Awaitable[None]]

MAX_TOOL_ROUNDS = 5

# ─── 路由 LLM Prompt ─────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """你是音频生成意图路由器。分析用户消息，输出 JSON 路由决策。

意图域：
- audio_generate：首次生成音频（无上次记录，或用户明确要"重新生成"）
  ✅ 触发词：「生成一首歌」「帮我生成音乐」「用 MiniMax/Suno 生成」「生成配乐」「参考旋律生成」
  ✅ 消息含 ABC 旋律符号（如 z8|[DA_g]4 等）且要求「生成音乐/歌曲」→ 必须是 audio_generate！
  ⚠️ 【MINIMAX】前缀 + 「生成一首歌」= audio_generate，不是 voice_clone！
- audio_iterate：在上次基础上改进（有上次记录，用户说"再X一点"/"换成X风格"等）
- audio_cover：翻唱（用户提供了音频 URL 或说"翻唱"）
- voice_clone：音色克隆，仅限用户明确说「克隆声音/音色」「用我的声音」「上传音色」「复刻音色」「查看音色列表」
  ⚠️ 「生成一首歌」「生成音乐」绝对不是 voice_clone！ABC 旋律内容不是「参考音频」！

输出 JSON：
{
  "domain": "audio_generate|audio_iterate|audio_cover|voice_clone",
  "confidence": 0.0-1.0,
  "provider": "minimax|suno|auto",
  "use_current_abc": true/false,
  "style_hint": "用户描述的风格（如有）",
  "cover_url": "翻唱源 URL（cover 时）",
  "voice_clone_intent": "upload_and_clone|synthesize|list_voices（voice_clone 域时填写）",
  "summary": "一句话说明路由决策"
}
"""

# ─── Audio Agent System Prompt（动态构建，每次请求注入最新服务状态）────────────

_AUDIO_AGENT_SYSTEM_TEMPLATE = """你是专业的 AI 音乐生成助手，通过调用工具生成和迭代改进音频。

工作原则：
1. 首次生成（audio_generate 域）：
   a. 若有 ABC 谱 → 【一次性同时调用】abc_to_audio_prompt 和 generate_lyrics_minimax（两者完全独立，并行执行节省时间）
   b. 拿到 prompt 和 lyrics 后，一次调用生成工具（generate_audio_minimax 或 generate_audio_suno）
   c. 生成完成，输出 JSON 结果。全程最多 3 次 LLM 推理轮次。
2. 迭代改进（audio_iterate 域）：【一次性同时调用】evolve_audio_prompt，拿到结果后调用生成工具
3. 翻唱（audio_cover 域）：直接调用 generate_cover_minimax
4. 音色克隆（voice_clone 域）：
   - 上传并克隆 → upload_voice_sample 获取 file_id → clone_voice_minimax 克隆
   - 用克隆音色合成语音 → synthesize_speech_minimax（需 voice_id）
   - 查看已有音色 → list_cloned_voices
5. 服务商选择（必须参考下方【当前可用服务状态】，❌ 标记的工具本轮严禁调用）：
   - 纯音乐/快速预览 → generate_audio_minimax（model=music-2.6-free）
   - 有歌词的歌曲 → 优先 generate_audio_suno，不可用时直接用 generate_audio_minimax + lyrics 参数
   - 高质量 → generate_audio_minimax（model=music-2.6）或 generate_audio_suno（model=chirp-v5-5）
6. 【重要】generate_audio_minimax 和 generate_cover_minimax 已内置自动落盘：
   - 工具内部会自动将音频下载保存到 workspace，并在返回结果中包含 workspace_path 字段
   - 调用这两个工具后，【禁止】再调用 save_audio_from_url 重复保存，否则会产生两个相同文件
   - 只有当工具返回的 workspace_path 为空字符串时，才允许调用 save_audio_from_url 作为兜底
7. 生成完成后，提供 2-3 个迭代建议
8. 【效率要求】尽量在一次 LLM 推理中输出所有可并行的工具调用，减少推理轮次

{service_status_block}

完成所有工具调用后，用 JSON 格式回复最终结果：
{{
  "audio_url": "...",
  "provider": "minimax|suno",
  "prompt_used": "...",
  "style_used": "...",
  "instrumental": false,
  "duration_ms": 0,
  "voice_id": "（音色克隆域：克隆或使用的 voice_id；其他域留空）",
  "summary": "一句话描述本次生成",
  "suggestions": ["建议1", "建议2"]
}}
"""


def _build_audio_agent_system() -> str:
    """每次请求动态构建 System Prompt，注入最新服务可用性状态"""
    return _AUDIO_AGENT_SYSTEM_TEMPLATE.format(
        service_status_block=service_health.prompt_block()
    )


class AudioChatRunner:
    """
    对话式音频生成 Runner。
    每次调用传入完整的 audio_history，Runner 自动判断是首次还是迭代。
    """

    async def run(
        self,
        user_message: str,
        audio_history: list[dict],
        score_meta: ScoreMeta | None,
        current_abc: str,
        publish: Publisher,
        audio_b64: str = "",
        session_id: str = "",   # 传入后自动落库 assistant/tool 消息（刷新恢复）
    ) -> dict:
        """
        返回：
        {
          "audio_url": str,
          "provider": str,
          "prompt_used": str,
          "style_used": str,
          "lyrics_used": str,
          "instrumental": bool,
          "duration_ms": int,
          "summary": str,
          "suggestions": [...],
          "diff_summary": str,   # 与上次的差异说明（迭代时有值）
          "tool_calls": [...],
          "turn": int,           # 第几轮对话
        }
        """
        turn = len(audio_history) + 1
        last = audio_history[-1] if audio_history else None

        await publish("pipeline.step", {
            "step": "audio_router",
            "status": "running",
            "text": f"分析音频意图（第 {turn} 轮）...",
        })

        # ── Step 1: 路由意图识别 ────────────────────────────────────────────
        domain, route_params = await self._route(user_message, last, score_meta)

        await publish("pipeline.step", {
            "step": "audio_router",
            "status": "succeeded",
            "text": f"意图识别：{domain}（{route_params.get('summary', '')}）",
        })

        # ── Step 2: 工具准备（voice_clone 域已迁移到 VoiceCloneAgent/sovits 域）──
        # v2.0 架构：音色克隆统一由 VoiceCloneAgent 处理（sovits 域），
        # audio_runner 只负责音乐生成（generate/iterate/cover）。
        # voice_clone 域在此作为兜底，提示用户走正确入口。
        tools = get_tool_schemas("audio")
        tool_call_records: list[dict] = []
        pre_uploaded_file_id = ""

        # ── Step 3: 构造 Audio Agent 上下文 ────────────────────────────────
        # 注意：pre_uploaded_file_id 替代 audio_b64 注入 context，LLM 不接触原始 base64
        user_content = self._build_user_content(
            user_message, domain, route_params,
            last, score_meta, current_abc, pre_uploaded_file_id
        )

        messages: list[dict] = [
            {"role": "system", "content": _build_audio_agent_system()},
            {"role": "user", "content": user_content},
        ]

        await publish("pipeline.step", {
            "step": "audio_agent",
            "status": "running",
            "text": "Audio Agent 开始处理...",
        })

        # ── Step 4: Tool-Calling Loop（支持并行工具调用）──────────────────────
        import uuid as _uuid
        from app.pipeline import db as _db
        import logging as _log
        _audio_logger = _log.getLogger("ep_agent.audio_runner")

        latest_result: dict = {}

        for _round in range(MAX_TOOL_ROUNDS):
            response = await complete_with_tools(messages, tools, temperature=0.3)
            content_this_round = response["content"] or ""
            tool_calls_this_round = response["tool_calls"] or []

            messages.append({
                "role": "assistant",
                "content": content_this_round,
                "tool_calls": tool_calls_this_round,
            })

            # ── 落库本轮 assistant 消息 ──────────────────────────────────────
            if session_id:
                try:
                    _db.insert_message(
                        msg_id=f"asst_{_uuid.uuid4().hex[:12]}",
                        session_id=session_id,
                        role="assistant",
                        content=content_this_round,
                        tool_calls=tool_calls_this_round if tool_calls_this_round else None,
                    )
                except Exception as _e:
                    _audio_logger.warning(
                        "[AudioRunner] assistant 消息落库失败 session=%s round=%d: %s",
                        session_id, _round, _e
                    )

            if response["finish_reason"] == "stop" or not tool_calls_this_round:
                raw = content_this_round
                try:
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    if start >= 0:
                        latest_result = json.loads(raw[start:end])
                except Exception:
                    latest_result = {"summary": raw, "audio_url": ""}
                break

            # ── 并行执行本轮所有工具调用 ────────────────────────────────────
            # LLM 可能在一次推理中输出多个工具调用（parallel tool calls），
            # 用 asyncio.gather 并发执行，消除串行等待。
            async def _exec_one_tool(tc: dict) -> tuple[dict, str, str | None]:
                """执行单个工具调用，返回 (tc, result_str, error_msg)"""
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                await publish("tool.call", {
                    "call_id": tc["id"],
                    "tool": tool_name,
                    "arguments": {k: v for k, v in arguments.items()
                                  if k not in ("lyrics", "abc")},
                    "status": "running",
                })

                try:
                    result = await call_tool(tool_name, arguments)
                    result_str = (
                        result if isinstance(result, str)
                        else json.dumps(result, ensure_ascii=False)
                    )
                    return tc, tool_name, arguments, result, result_str, None
                except Exception as e:
                    return tc, tool_name, arguments, {}, f"工具执行失败: {e}", str(e)

            # 并发执行所有工具
            exec_results = await asyncio.gather(
                *[_exec_one_tool(tc) for tc in tool_calls_this_round],
                return_exceptions=False
            )

            # ── 按顺序处理结果（保持 messages 顺序一致）──────────────────────
            for tc, tool_name, arguments, result, result_str, err in exec_results:
                if err:
                    # 工具执行失败
                    tool_call_records.append({
                        "id": tc["id"], "tool": tool_name,
                        "arguments": arguments, "status": "failed", "error": err,
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"],
                        "tool": tool_name, "status": "failed", "error": err,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })
                    if session_id:
                        try:
                            _db.insert_message(
                                msg_id=f"tool_{tc['id']}_{_uuid.uuid4().hex[:8]}",
                                session_id=session_id, role="tool",
                                content=result_str[:4096],
                                tool_call_id=tc["id"], tool_name=tool_name,
                            )
                        except Exception as _e:
                            _audio_logger.warning(
                                "[AudioRunner] tool 失败消息落库失败 session=%s tool=%s: %s",
                                session_id, tool_name, _e
                            )
                    continue

                # 捕获生成结果
                if tool_name in ("generate_audio_minimax", "generate_audio_suno",
                                 "generate_cover_minimax", "save_audio_from_url"):
                    if isinstance(result, dict):
                        latest_result = result

                # ── 防重复落盘拦截 ──────────────────────────────────────────
                if (
                    tool_name == "save_audio_from_url"
                    and isinstance(latest_result, dict)
                    and latest_result.get("workspace_path")
                ):
                    _audio_logger.info(
                        "[AudioRunner] 跳过重复 save_audio_from_url，"
                        "workspace_path 已存在: %s",
                        latest_result["workspace_path"]
                    )
                    result = {
                        "workspace_path": latest_result["workspace_path"],
                        "filename": latest_result["workspace_path"].split("/")[-1],
                        "success": True,
                        "error": "",
                        "_skipped_reason": "文件已由生成工具自动落盘，跳过重复下载",
                    }
                    result_str = json.dumps(result, ensure_ascii=False)

                tool_call_records.append({
                    "id": tc["id"], "tool": tool_name,
                    "arguments": {k: v for k, v in arguments.items()
                                  if k not in ("lyrics", "abc")},
                    "result_preview": result_str[:120] + "..." if len(result_str) > 120 else result_str,
                    "status": "succeeded",
                })
                await publish("tool.call", {
                    "call_id": tc["id"],
                    "tool": tool_name, "status": "succeeded",
                    "result_preview": result_str[:80] + "..." if len(result_str) > 80 else result_str,
                    "full_result": result_str[:4096],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })
                if session_id:
                    try:
                        _db.insert_message(
                            msg_id=f"tool_{tc['id']}_{_uuid.uuid4().hex[:8]}",
                            session_id=session_id, role="tool",
                            content=result_str[:4096],
                            tool_call_id=tc["id"], tool_name=tool_name,
                        )
                    except Exception as _e:
                        _audio_logger.warning(
                            "[AudioRunner] tool 消息落库失败 session=%s tool=%s: %s",
                            session_id, tool_name, _e
                        )

        # ── Step 5: 构造本轮记录 ────────────────────────────────────────────
        audio_record = {
            "turn":         turn,
            "user_message": user_message,
            "domain":       domain,
            "prompt":       latest_result.get("prompt_used", ""),
            "style":        latest_result.get("style_used", ""),
            "lyrics":       latest_result.get("lyrics_used", ""),
            "instrumental": latest_result.get("instrumental", False),
            "provider":     latest_result.get("provider", ""),
            "model":        latest_result.get("model", ""),
            "audio_url":    latest_result.get("audio_url", ""),
            "audio_b64":    latest_result.get("audio_b64", ""),
            "workspace_path": latest_result.get("workspace_path", ""),
            "duration_ms":  latest_result.get("duration_ms", 0),
            "summary":      latest_result.get("summary", ""),
            "suggestions":  latest_result.get("suggestions", []),
            # voice_clone 域专属字段
            "voice_id":     latest_result.get("voice_id", ""),
        }

        # 计算 diff（迭代时）
        diff_summary = ""
        if domain == "audio_iterate" and last:
            try:
                diff = await call_tool("diff_audio_params", {
                    "params_before": {
                        "prompt": last.get("prompt", ""),
                        "style": last.get("style", ""),
                        "instrumental": last.get("instrumental", False),
                        "provider": last.get("provider", ""),
                    },
                    "params_after": {
                        "prompt": audio_record["prompt"],
                        "style": audio_record["style"],
                        "instrumental": audio_record["instrumental"],
                        "provider": audio_record["provider"],
                    },
                })
                diff_summary = diff.get("summary", "")
            except Exception:
                pass

        # voice_clone 域：克隆成功（有 voice_id）、TTS 成功（有 audio_url）、
        # 或查询列表成功（list_voices 意图无 voice_id 也是成功）
        intent = route_params.get("voice_clone_intent", "")
        is_voice_clone_ok = domain == "voice_clone" and (
            bool(audio_record.get("voice_id"))   # 克隆成功
            or bool(audio_record.get("audio_url"))  # TTS 合成成功
            or intent == "list_voices"              # 查询列表成功
        )
        is_success = bool(audio_record["audio_url"]) or is_voice_clone_ok
        await publish("pipeline.step", {
            "step": "audio_agent",
            "status": "succeeded" if is_success else "failed",
            "text": audio_record["summary"] or "音频生成完成",
        })

        return {
            **audio_record,
            "diff_summary": diff_summary,
            "tool_calls":   tool_call_records,
        }

    async def _route(
        self,
        user_message: str,
        last: dict | None,
        score_meta: ScoreMeta | None,
    ) -> tuple[str, dict]:
        """快速路由：识别意图域"""
        context = ""
        if last:
            context = f"上次生成记录：{json.dumps(last, ensure_ascii=False)[:300]}"
        if score_meta:
            context += f"\n当前谱子：{score_meta.title}，{score_meta.key}，{score_meta.bpm:.0f}BPM"

        raw = await complete([
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user", "content": f"{context}\n\n用户消息：{user_message}"},
        ], temperature=0.1)

        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            params = json.loads(raw[start:end])
            domain = params.get("domain", "audio_generate")
            # 无历史时强制为 generate
            if not last and domain == "audio_iterate":
                domain = "audio_generate"
            return domain, params
        except Exception:
            return "audio_generate", {"summary": "默认生成模式"}

    def _build_user_content(
        self,
        user_message: str,
        domain: str,
        route_params: dict,
        last: dict | None,
        score_meta: ScoreMeta | None,
        current_abc: str,
        pre_uploaded_file_id: str = "",   # Runner 层预上传后的 file_id，非原始 base64
    ) -> str:
        parts = [f"用户请求：{user_message}", f"意图域：{domain}"]

        if score_meta:
            parts.append(
                f"当前谱子：标题={score_meta.title}, 调号={score_meta.key}, "
                f"BPM={score_meta.bpm:.0f}, 音符数={score_meta.note_count}"
            )

        if current_abc:
            parts.append(f"当前 ABC 谱（前200字符）：\n{current_abc[:200]}")

        if domain == "audio_iterate" and last:
            parts.append(
                f"\n上次生成参数：\n"
                f"  prompt: {last.get('prompt', '')}\n"
                f"  style: {last.get('style', '')}\n"
                f"  lyrics: {last.get('lyrics', '')[:100] if last.get('lyrics') else '无'}\n"
                f"  instrumental: {last.get('instrumental', False)}\n"
                f"  provider: {last.get('provider', '')}\n"
                f"  audio_url: {last.get('audio_url', '')}"
            )
            parts.append(
                "请先调用 evolve_audio_prompt 进化参数，再调用生成工具重新生成。"
            )
        elif domain == "audio_generate":
            if current_abc:
                parts.append("请先调用 abc_to_audio_prompt 提取谱子特征，再生成音频。")
            else:
                parts.append("请直接根据用户描述构造 prompt 并生成音频。")
        elif domain == "voice_clone":
            # v2.0：音色克隆已迁移到 VoiceCloneAgent（sovits 域），
            # 此处仅作提示，引导用户走正确路径。
            parts.append(
                "音色克隆请求应通过 sovits 域处理。"
                "请告知用户：音色克隆功能由专属的音色克隆专家负责，"
                "请直接描述您的克隆需求（如：'帮我克隆这段声音'），系统会自动路由到正确的处理流程。"
            )

        if route_params.get("cover_url"):
            parts.append(f"翻唱源音频：{route_params['cover_url']}")

        return "\n".join(parts)


audio_chat_runner = AudioChatRunner()
