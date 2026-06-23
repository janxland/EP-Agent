"""
音频对话路由 - /api/sessions/:id/audio/chat

对话式音频生成端点：支持首次生成 + 迭代改进（"再欢快一点"式交互）

端点：
  POST /api/sessions/{session_id}/audio/chat   - 发送消息生成/迭代音频
  GET  /api/sessions/{session_id}/audio/history - 获取音频对话历史
  DELETE /api/sessions/{session_id}/audio/history - 清空音频对话历史（重新开始）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.pipeline import service
from app.pipeline.router import _make_publisher

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
    prompt_used: str
    style_used: str
    lyrics_used: str = ""
    instrumental: bool
    duration_ms: int
    summary: str
    suggestions: list[str]
    diff_summary: str = ""
    tool_calls: list[dict] = []
    domain: str = ""
    # voice_clone 域专属
    voice_id: str = ""          # 克隆或使用的音色 ID
    demo_audio: str = ""        # 克隆后试听音频 URL


# ─── 路由处理 ─────────────────────────────────────────────────────────────────

@router.post("/{session_id}/audio/chat")
async def audio_chat(session_id: str, req: AudioChatRequest) -> AudioChatResponse:
    """
    对话式音频生成。

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
        return AudioChatResponse(**result)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
