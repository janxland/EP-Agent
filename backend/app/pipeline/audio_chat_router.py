"""
音频对话路由 - /api/sessions/:id/audio/chat

对话式音频生成端点：支持首次生成 + 迭代改进（"再欢快一点"式交互）

端点：
  POST /api/sessions/{session_id}/audio/chat         - 发送消息生成/迭代音频（同步，兼容旧版）
  POST /api/sessions/{session_id}/audio/chat/stream  - 流式 SSE 输出（推荐，实时进度）
  GET  /api/sessions/{session_id}/audio/history      - 获取音频对话历史
  DELETE /api/sessions/{session_id}/audio/history    - 清空音频对话历史（重新开始）
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.pipeline import service
from app.pipeline.routers.hub import _make_publisher
from app.pipeline.domain import new_id

router = APIRouter(prefix="/api/sessions", tags=["audio-chat"])


# ─── 请求/响应模型 ────────────────────────────────────────────────────────────

class AudioChatRequest(BaseModel):
    message: str = Field(..., description="用户消息，如'给这首谱子配乐'、'再欢快一点'、'克隆我的声音'")
    provider: str = Field("auto", description="服务商偏好：auto | minimax | suno")
    # voice_clone 域：用户附带的音频 base64（前端读取文件后转 base64 传入）
    audio_b64: str = Field("", description="音色克隆源音频 base64（mp3/wav，10s-5min，≤20MB）；非克隆场景留空")


class AudioChatResponse(BaseModel):
    turn: int
    audio_url: str
    audio_b64: str = ""
    provider: str
    # 字段名与前端 AudioTurn 对齐（audio_runner 返回 prompt_used/style_used/lyrics_used，此处做映射）
    prompt: str = ""            # 对应 audio_runner 的 prompt_used
    style: str = ""             # 对应 audio_runner 的 style_used
    lyrics: str = ""            # 对应 audio_runner 的 lyrics_used
    model: str = ""             # 生成所用模型名称
    user_message: str = ""      # 本轮用户原始消息
    instrumental: bool = False
    duration_ms: int = 0
    summary: str = ""
    suggestions: list[str] = []
    diff_summary: str = ""
    tool_calls: list[dict] = []
    domain: str = ""
    # voice_clone 域专属
    voice_id: str = ""          # 克隆或使用的音色 ID
    demo_audio: str = ""        # 克隆后试听音频 URL


# ─── 内部工具：构造 SSE 行 ────────────────────────────────────────────────────

def _sse_line(evt_type: str, payload: dict, session_id: str = "") -> str:
    """序列化一条 SSE data 行（与 hub._publish 格式一致）"""
    evt = {
        "id":         new_id("evt"),
        "type":       evt_type,
        "session_id": session_id,
        "display":    True,
        "sequence":   -1,          # 流式端点不维护全局序列号
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "payload":    payload,
    }
    return f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"


# ─── 路由处理 ─────────────────────────────────────────────────────────────────

@router.post("/{session_id}/audio/chat")
async def audio_chat(session_id: str, req: AudioChatRequest) -> AudioChatResponse:
    """
    对话式音频生成（同步版，兼容旧版前端）。

    - 首次发送：生成新音频（自动从 ABC 谱提取 prompt）
    - 后续消息：在上次基础上迭代改进（"再欢快一点"、"换成爵士风"等）
    - 翻唱：消息中包含音频 URL 或说"翻唱"时自动切换模式

    返回本轮生成结果，并自动保存到 Session 的 audio_history。
    """
    try:
        result = await service.audio_chat(
            session_id=session_id,
            message=req.message,
            provider=req.provider,
            audio_b64=req.audio_b64,
            publish=_make_publisher(session_id),
        )
        # audio_runner 返回 prompt_used/style_used/lyrics_used，映射为前端期望的字段名
        mapped = {
            **result,
            "prompt":       result.get("prompt_used", ""),
            "style":        result.get("style_used",  ""),
            "lyrics":       result.get("lyrics_used", ""),
            "model":        result.get("model",        ""),
            "user_message": req.message,
        }
        return AudioChatResponse(**mapped)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{session_id}/audio/chat/stream")
async def audio_chat_stream(session_id: str, req: AudioChatRequest):
    """
    对话式音频生成 — SSE 流式输出（推荐）。

    实时推送以下事件（与主 SSE 流格式一致）：
      pipeline.step  — 路由识别、Agent 处理等阶段进度
      tool.call      — 每个工具调用的开始/完成/失败
      audio.result   — 最终生成结果（type=audio.result，payload 含完整 AudioChatResponse 字段）
      audio.error    — 出错时推送错误信息

    前端通过 fetch + ReadableStream 读取，无需 EventSource。
    """

    async def event_generator() -> AsyncIterator[str]:
        # 收集流式事件的内部队列
        stream_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)

        async def stream_publish(evt_type: str, payload: dict, display: bool = True):
            """双路推送：既写入流式队列（供本次响应），也推送到全局 SSE hub（供主 stream 展示）"""
            # 1. 写入本次 SSE 响应流
            line = _sse_line(evt_type, payload, session_id)
            try:
                stream_queue.put_nowait(line)
            except asyncio.QueueFull:
                pass
            # 2. 同步推送到主 SSE hub（让已打开 /stream 的前端页面也能看到进度）
            from app.pipeline.routers.hub import _publish as _hub_publish
            await _hub_publish(session_id, evt_type, payload, display=display)

        # 在后台任务中运行 audio_chat，publish 回调写入队列
        async def run_audio():
            try:
                result = await service.audio_chat(
                    session_id=session_id,
                    message=req.message,
                    provider=req.provider,
                    audio_b64=req.audio_b64,
                    publish=stream_publish,
                )
                # 映射字段名
                mapped = {
                    **result,
                    "prompt":       result.get("prompt_used", ""),
                    "style":        result.get("style_used",  ""),
                    "lyrics":       result.get("lyrics_used", ""),
                    "model":        result.get("model",        ""),
                    "user_message": req.message,
                }
                # 推送最终结果事件
                await stream_publish("audio.result", mapped, display=True)
            except Exception as exc:
                await stream_publish("audio.error", {
                    "error": str(exc),
                    "message": req.message,
                }, display=True)
            finally:
                # 发送结束哨兵
                await stream_queue.put(None)

        # 启动后台任务
        task = asyncio.create_task(run_audio())

        # 发送连接确认
        yield _sse_line("audio.connected", {"session_id": session_id, "message": req.message}, session_id)

        # 持续读取队列，直到收到 None 哨兵
        try:
            while True:
                try:
                    # timeout=360s：覆盖 Suno 最长 300s 生成 + 网络余量
                    item = await asyncio.wait_for(stream_queue.get(), timeout=360.0)
                except asyncio.TimeoutError:
                    # 超时保活（每 360s 发一次，防代理层断连）
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        # 发送流结束标记
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@router.get("/{session_id}/audio/history")
async def get_audio_history(session_id: str):
    """获取当前 Session 的音频对话历史（所有轮次）"""
    try:
        sess = service.get_session(session_id)
        return {
            "session_id": session_id,
            "total_turns": len(sess.audio_history),
            "history": sess.audio_history,
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{session_id}/audio/history")
async def clear_audio_history(session_id: str):
    """清空音频对话历史，下次生成将视为首次生成"""
    try:
        sess = service.get_session(session_id)
        cleared_count = len(sess.audio_history)
        sess.audio_history = []
        service.save_session(sess)
        return {
            "session_id": session_id,
            "cleared_turns": cleared_count,
            "message": "音频对话历史已清空",
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
