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
import json
from typing import Callable, Awaitable

from app.agentcore.llm import complete, complete_with_tools
from app.pipeline.domain import ScoreMeta
from app.agentcore.tools import get_tool_schemas, call_tool

Publisher = Callable[[str, dict], Awaitable[None]]

MAX_TOOL_ROUNDS = 5

# ─── 路由 LLM Prompt ─────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """你是音频生成意图路由器。分析用户消息，输出 JSON 路由决策。

意图域：
- audio_generate：首次生成音频（无上次记录，或用户明确要"重新生成"）
- audio_iterate：在上次基础上改进（有上次记录，用户说"再X一点"/"换成X风格"等）
- audio_cover：翻唱（用户提供了音频 URL 或说"翻唱"）
- voice_clone：音色克隆（用户说"克隆声音"/"用我的声音"/"上传音色"/"复刻音色"/"用这个声音合成"/"查看音色"等）

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

# ─── Audio Agent System Prompt ────────────────────────────────────────────────

_AUDIO_AGENT_SYSTEM = """你是专业的 AI 音乐生成助手，通过调用工具生成和迭代改进音频。

工作原则：
1. 首次生成：用 abc_to_audio_prompt 提取谱子特征，再调用生成工具
2. 迭代改进：先调用 evolve_audio_prompt 进化参数，再调用生成工具
3. 翻唱：直接调用 generate_cover_minimax
4. 音色克隆（voice_clone 域）：
   - 上传并克隆 → upload_voice_sample 获取 file_id → clone_voice_minimax 克隆
   - 用克隆音色合成语音 → synthesize_speech_minimax（需 voice_id）
   - 查看已有音色 → list_cloned_voices
5. 服务商选择：
   - 纯音乐/快速预览 → generate_audio_minimax（model=music-2.6-free）
   - 有歌词的歌曲 → generate_audio_suno
   - 高质量 → generate_audio_minimax（model=music-2.6）或 generate_audio_suno（model=chirp-v5-5）
6. 生成完成后，提供 2-3 个迭代建议

完成所有工具调用后，用 JSON 格式回复最终结果：
{
  "audio_url": "...",
  "provider": "minimax|suno",
  "prompt_used": "...",
  "style_used": "...",
  "instrumental": false,
  "duration_ms": 0,
  "voice_id": "（音色克隆域：克隆或使用的 voice_id；其他域留空）",
  "summary": "一句话描述本次生成",
  "suggestions": ["建议1", "建议2"]
}
"""


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

        # ── Step 2: voice_clone 域预上传（Runner 层消费 audio_b64，不经 LLM）──
        tools = get_tool_schemas("audio")
        tool_call_records: list[dict] = []
        pre_uploaded_file_id = ""

        if domain == "voice_clone" and audio_b64:
            intent = route_params.get("voice_clone_intent", "upload_and_clone")
            if intent == "upload_and_clone":
                await publish("pipeline.step", {
                    "step": "voice_upload",
                    "status": "running",
                    "text": "正在上传音色样本...",
                })
                try:
                    upload_result = await call_tool("upload_voice_sample", {
                        "audio_b64": audio_b64,
                        "filename": "voice_sample.mp3",
                    })
                    pre_uploaded_file_id = (
                        upload_result.get("file_id", "")
                        if isinstance(upload_result, dict) else ""
                    )
                    await publish("pipeline.step", {
                        "step": "voice_upload",
                        "status": "succeeded",
                        "text": f"音色样本上传成功，file_id={pre_uploaded_file_id}",
                    })
                    # 记录预上传工具调用
                    tool_call_records.append({
                        "id": "pre_upload",
                        "tool": "upload_voice_sample",
                        "arguments": {"filename": "voice_sample.mp3"},
                        "result_preview": f"file_id={pre_uploaded_file_id}",
                        "status": "succeeded",
                    })
                except Exception as e:
                    await publish("pipeline.step", {
                        "step": "voice_upload",
                        "status": "failed",
                        "text": f"音色样本上传失败：{e}",
                    })
                    # 上传失败则提前返回，避免 Agent 用空 file_id 继续
                    return {
                        "turn": turn, "user_message": user_message,
                        "domain": domain, "audio_url": "", "voice_id": "",
                        "summary": f"音色样本上传失败：{e}",
                        "suggestions": ["请检查音频格式（mp3/wav）和文件大小（10s-5min）"],
                        "diff_summary": "", "tool_calls": tool_call_records,
                    }

        # ── Step 3: 构造 Audio Agent 上下文 ────────────────────────────────
        # 注意：pre_uploaded_file_id 替代 audio_b64 注入 context，LLM 不接触原始 base64
        user_content = self._build_user_content(
            user_message, domain, route_params,
            last, score_meta, current_abc, pre_uploaded_file_id
        )

        messages: list[dict] = [
            {"role": "system", "content": _AUDIO_AGENT_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        await publish("pipeline.step", {
            "step": "audio_agent",
            "status": "running",
            "text": "Audio Agent 开始处理...",
        })

        # ── Step 4: Tool-Calling Loop ───────────────────────────────────────
        latest_result: dict = {}

        for _round in range(MAX_TOOL_ROUNDS):
            response = await complete_with_tools(messages, tools, temperature=0.3)
            messages.append({
                "role": "assistant",
                "content": response["content"],
                "tool_calls": response["tool_calls"] or [],
            })

            if response["finish_reason"] == "stop" or not response["tool_calls"]:
                # LLM 给出最终文本结果
                raw = response["content"] or ""
                try:
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    if start >= 0:
                        latest_result = json.loads(raw[start:end])
                except Exception:
                    latest_result = {"summary": raw, "audio_url": ""}
                break

            # 执行工具调用
            for tc in response["tool_calls"]:
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

                    # 捕获生成结果
                    if tool_name in ("generate_audio_minimax", "generate_audio_suno",
                                     "generate_cover_minimax"):
                        if isinstance(result, dict):
                            latest_result = result

                    tool_call_records.append({
                        "id": tc["id"],
                        "tool": tool_name,
                        "arguments": {k: v for k, v in arguments.items()
                                      if k not in ("lyrics", "abc")},
                        "result_preview": result_str[:120] + "..." if len(result_str) > 120 else result_str,
                        "status": "succeeded",
                    })

                    await publish("tool.call", {
                        "call_id": tc["id"],
                        "tool": tool_name,
                        "status": "succeeded",
                        "result_preview": result_str[:80] + "..." if len(result_str) > 80 else result_str,
                    })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_str,
                    })

                except Exception as e:
                    tool_call_records.append({
                        "id": tc["id"], "tool": tool_name,
                        "arguments": arguments, "status": "failed", "error": str(e),
                    })
                    await publish("tool.call", {
                        "call_id": tc["id"],
                        "tool": tool_name, "status": "failed", "error": str(e),
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"工具执行失败: {e}",
                    })

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

        # voice_clone 域：有 voice_id 或查询成功即为成功（无需 audio_url）
        is_voice_clone_ok = domain == "voice_clone" and bool(audio_record.get("voice_id"))
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
            intent = route_params.get("voice_clone_intent", "upload_and_clone")
            if intent == "list_voices":
                parts.append("请调用 list_cloned_voices 查询已克隆的音色列表。")
            elif intent == "synthesize":
                parts.append(
                    "请调用 synthesize_speech_minimax 用指定 voice_id 合成语音。"
                    "若用户未提供 voice_id，先调用 list_cloned_voices 让用户选择。"
                )
            else:  # upload_and_clone
                if pre_uploaded_file_id:
                    # Runner 层已完成上传，Agent 直接从 clone 步骤开始
                    parts.append(
                        f"音频样本已由系统预上传，file_id={pre_uploaded_file_id}。\n"
                        f"请按以下顺序调用工具完成音色克隆：\n"
                        f"1. clone_voice_minimax(file_id='{pre_uploaded_file_id}', "
                        f"voice_id='user_clone_<随机4位英文字母数字>', "
                        f"preview_text='你好，这是我的克隆音色')\n"
                        f"2. synthesize_speech_minimax(text='你好，这是我的克隆音色', "
                        f"voice_id=<上一步返回的voice_id>)\n"
                        f"完成后在 JSON 结果中填写 voice_id 和 audio_url 字段。"
                    )
                else:
                    parts.append(
                        "用户未上传音频，请回复提示：需要提供 10s-5min 的 mp3/wav 音频文件。\n"
                        "若用户已说明 voice_id，可跳过上传直接调用 synthesize_speech_minimax。"
                    )

        if route_params.get("cover_url"):
            parts.append(f"翻唱源音频：{route_params['cover_url']}")

        return "\n".join(parts)


audio_chat_runner = AudioChatRunner()
