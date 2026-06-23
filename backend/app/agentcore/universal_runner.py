"""
Universal Chat Runner — 统一意图路由器

用户发一条消息（可附带文件内容），LLM 自动识别意图并路由到正确的执行路径：

  意图域：
    convert   → 用户粘贴了 Sky JSON，先转换为 ABC
    edit      → 修改谱子（转调/变速/风格/加花等），LLM 直接输出新 ABC
    audio     → 生成/迭代音频，走 audio_runner
    voice     → 音色克隆相关，走 audio_runner（voice_clone 域）
    query     → 查询/分析谱子信息，LLM 直接回答

这样用户不需要知道有哪些接口，直接说话/粘贴文件就能得到结果。
500 "no score in session" 也不会再出现——convert 意图会自动先建谱子。
"""
from __future__ import annotations
import json
import re
from typing import Callable, Awaitable

from app.agentcore.llm import complete
from app.pipeline.domain import ScoreMeta

Publisher = Callable[[str, dict], Awaitable[None]]

# ─── 意图路由 Prompt ──────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """你是 EP-Agent 的意图路由器。分析用户消息和附件内容，输出 JSON 路由决策。

意图域：
- convert  : 用户提供了 Sky JSON 谱子内容（消息中含有 songNotes 字段或 JSON 数组格式的谱子）
- edit     : 修改已有谱子（转调/变速/风格/加花/重写等），必须已有谱子
- audio    : 生成音乐/音频（"生成配乐"/"再欢快一点"/"翻唱"等）
- voice    : 音色克隆（"克隆声音"/"用我的声音"/"查看音色"等）
- query    : 查询/分析谱子信息（"这首曲子是什么调"/"有多少音符"等）

输出严格 JSON，不要任何其他文字：
{
  "domain": "convert|edit|audio|voice|query",
  "confidence": 0.0-1.0,
  "has_attachment": true/false,
  "attachment_type": "sky_json|text|midi|audio|none",
  "summary": "一句话说明路由决策"
}
"""


async def _route_intent(
    message: str,
    attachment_name: str,
    attachment_preview: str,
    has_score: bool,
) -> dict:
    """调用 LLM 识别意图域"""
    context_parts = [f"用户消息：{message}"]
    if attachment_name:
        context_parts.append(f"附件名称：{attachment_name}")
    if attachment_preview:
        context_parts.append(f"附件内容预览（前500字）：\n{attachment_preview[:500]}")
    context_parts.append(f"当前 session 是否已有谱子：{'是' if has_score else '否'}")

    messages = [
        {"role": "system", "content": _ROUTER_SYSTEM},
        {"role": "user",   "content": "\n".join(context_parts)},
    ]
    resp = await complete(messages)
    raw = resp if isinstance(resp, str) else resp.get("content", "{}")
    # 提取 JSON
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {"domain": "edit" if has_score else "query", "confidence": 0.5, "summary": "兜底路由"}


# ─── Universal Runner ─────────────────────────────────────────────────────────

class UniversalChatRunner:
    """
    统一对话 Runner。
    根据意图自动路由，不需要前端区分接口。
    """

    async def run(
        self,
        session_id: str,
        message: str,
        attachment_content: str,   # 附件文本内容（base64 已解码）
        attachment_name: str,      # 附件文件名
        attachment_b64: str,       # 音频附件 base64（音色克隆用）
        session_getter,            # service.get_session
        session_saver,             # service.save_session
        publish: Publisher,
        convert_fn,                # service.convert
        edit_fn,                   # service.edit
        audio_chat_fn,             # service.audio_chat
    ) -> dict:
        """
        执行统一对话，返回结果字典：
          {
            "domain": str,          # 实际执行的意图域
            "message": str,         # AI 回复文本
            "abc_updated": bool,    # 谱子是否更新
            "audio_url": str,       # 音频 URL（audio 域）
            "voice_id": str,        # 音色 ID（voice 域）
            ...                     # 其他域特有字段
          }
        """
        sess = session_getter(session_id)
        has_score = sess.score is not None

        await publish("pipeline.step", {
            "step": "routing",
            "status": "running",
            "text": "正在理解意图...",
        })

        # ── 意图路由 ──────────────────────────────────────────────────────────
        route = await _route_intent(
            message=message,
            attachment_name=attachment_name,
            attachment_preview=attachment_content,
            has_score=has_score,
        )
        domain = route.get("domain", "query")

        await publish("pipeline.step", {
            "step": "routing",
            "status": "succeeded",
            "text": f"意图识别：{domain} — {route.get('summary', '')}",
        })

        # ── convert 域：先转换 JSON → ABC ─────────────────────────────────────
        if domain == "convert":
            # 优先用附件内容，其次从消息中提取
            json_content = attachment_content or message
            file_name = attachment_name or "score.json"

            # 验证是否有 songNotes
            try:
                parsed = json.loads(json_content)
                arr = parsed if isinstance(parsed, list) else [parsed]
                if not arr[0].get("songNotes"):
                    # 不是有效 Sky JSON，降级为 edit
                    domain = "edit"
            except Exception:
                domain = "edit"

            if domain == "convert":
                try:
                    result = await convert_fn(session_id, json_content, file_name, publish)
                except Exception as e:
                    await publish("pipeline.step", {
                        "step": "convert", "status": "failed", "text": f"转换失败: {e}",
                    })
                    return {"domain": "convert", "message": f"谱子转换失败：{e}", "abc_updated": False}
                # 转换完成后，如果消息里还有编辑意图，继续 edit
                edit_hint = message.strip()
                if edit_hint and not any(kw in edit_hint for kw in [
                    "上传", "导入", "转换", "解析", "加载", "这个", "这首"
                ]):
                    sess2 = session_getter(session_id)
                    if sess2.score:
                        await publish("pipeline.step", {
                            "step": "chained_edit", "status": "running",
                            "text": f"谱子已加载，继续执行：{edit_hint}",
                        })
                        try:
                            edit_result = await edit_fn(session_id, edit_hint, publish)
                        except Exception:
                            edit_result = {}
                        er = edit_result if isinstance(edit_result, dict) else {}
                        return {
                            "domain": "convert+edit",
                            "message": f"已加载谱子《{result.get('meta', {}).get('title', '')}》并完成修改：{er.get('summary', '')}",
                            "abc_updated": True,
                            **er,
                        }
                return {
                    "domain": "convert",
                    "message": f"已成功加载谱子《{result.get('meta', {}).get('title', '')}》，共 {result.get('meta', {}).get('note_count', 0)} 个音符",
                    "abc_updated": True,
                    **result,
                }

        # ── edit 域：LLM 直接修改 ABC ─────────────────────────────────────────
        if domain == "edit":
            if not has_score:
                await publish("pipeline.step", {
                    "step": "edit", "status": "failed",
                    "text": "请先上传 Sky JSON 谱子，再进行修改",
                })
                msg = "请先上传 Sky JSON 谱子文件，我才能帮你修改 😊\n\n你可以直接把谱子文件内容粘贴到对话框。"
                await publish("message.completed", {"message": msg})
                return {"domain": "edit", "message": msg, "abc_updated": False}
            try:
                result = await edit_fn(session_id, message, publish)
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "edit", "status": "failed", "text": f"编辑失败: {e}",
                })
                return {"domain": "edit", "message": f"编辑失败：{e}", "abc_updated": False}
            r = result if isinstance(result, dict) else {}
            return {
                "domain": "edit",
                "message": r.get("summary", "修改完成"),
                "abc_updated": True,
                **r,
            }

        # ── audio / voice 域：走 audio_chat_runner ────────────────────────────
        if domain in ("audio", "voice"):
            try:
                result = await audio_chat_fn(
                    session_id=session_id,
                    message=message,
                    provider="auto",
                    audio_b64=attachment_b64,
                    publish=publish,
                )
            except Exception as e:
                await publish("pipeline.step", {
                    "step": "audio", "status": "failed", "text": f"音频生成失败: {e}",
                })
                return {"domain": domain, "message": f"音频生成失败：{e}", "abc_updated": False}
            r = result if isinstance(result, dict) else {}
            return {
                "domain": domain,
                "message": r.get("summary", ""),
                "abc_updated": False,
                **r,
            }

        # ── query 域：LLM 直接回答 ────────────────────────────────────────────
        sess = session_getter(session_id)
        score_context = ""
        if sess.score:
            m = sess.score.meta
            score_context = (
                f"当前谱子：《{m.title}》，调号={m.key}，BPM={m.bpm:.0f}，"
                f"拍号={m.time_sig_num}/{m.time_sig_den}，音符数={m.note_count}\n"
                f"ABC 谱：\n{sess.score.abc_notation[:800]}"
            )

        messages = [
            {"role": "system", "content": "你是 EP-Agent，专业的 Sky 游戏乐谱助手。用中文简洁回答用户问题。"},
            {"role": "user",   "content": f"{score_context}\n\n用户问题：{message}"},
        ]
        resp = await complete(messages)
        answer = resp if isinstance(resp, str) else resp.get("content", "")

        await publish("pipeline.step", {
            "step": "query",
            "status": "succeeded",
            "text": answer[:80],
        })

        return {
            "domain": "query",
            "message": answer,
            "abc_updated": False,
        }


universal_runner = UniversalChatRunner()
