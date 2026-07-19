"""
MiniMax API Standalone Backend
================================
FastAPI 服务器，直接封装 MiniMax Python SDK，提供所有 API 的 REST 端点。
支持流式输出 TTS、流式文本聊天。

启动方式:
    cp .env.example .env   # 填入你的 MINIMAX_API_KEY
    pip install -r requirements.txt
    python main.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

# ── SDK 导入 ──────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python-sdk"))
from minimax_api import AsyncMiniMax, Config, MiniMaxAPIError, Region

# ── 加载 .env ──────────────────────────────────────────────────────
load_dotenv()


# ===================================================================
# 配置
# ===================================================================
class Settings:
    api_key: str = os.getenv("MINIMAX_API_KEY", "")
    region: str = os.getenv("MINIMAX_REGION", "mainland")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()

# ── 全局客户端（在 lifespan 中初始化） ──────────────────────────────
client: Optional[AsyncMiniMax] = None


def get_client() -> AsyncMiniMax:
    if client is None:
        raise RuntimeError("MiniMax 客户端尚未初始化，请先设置有效的 API Key")
    return client


# ===================================================================
# Pydantic 请求/响应模型
# ===================================================================
class ChatRequest(BaseModel):
    model: str = "MiniMax-M3"
    messages: list[Dict[str, Any]]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = True


class TTSRequest(BaseModel):
    model: str = "speech-2.8-hd"
    text: str
    voice_id: str = "male-qn-tianye"
    speed: float = 1.0
    volume: Optional[float] = None
    pitch: Optional[int] = None
    format: str = "mp3"
    sample_rate: int = 32000
    bitrate: int = 128000
    stream: bool = False
    subtitle_enable: bool = False


class MusicRequest(BaseModel):
    model: str = "music-3.0"
    prompt: Optional[str] = None
    lyrics: Optional[str] = None
    duration: Optional[int] = None


class LyricsRequest(BaseModel):
    mode: str = "write_full_song"
    prompt: Optional[str] = None
    title: Optional[str] = None


class ImageRequest(BaseModel):
    model: str = "image-01"
    prompt: str
    aspect_ratio: Optional[str] = None
    subject_reference: Optional[list[Dict[str, Any]]] = None


class VideoRequest(BaseModel):
    model: str = "MiniMax-Hailuo-2.3"
    prompt: str
    first_frame_image: Optional[str] = None
    duration: Optional[int] = None
    resolution: Optional[str] = None


class VoiceCloneRequest(BaseModel):
    voice_id: str
    file_id: str
    clone_prompt: Optional[Dict[str, Any]] = None


class VoiceDesignRequest(BaseModel):
    prompt: str
    preview_text: str = "你好，我是通过 AI 设计的语音助手。"
    voice_id: Optional[str] = None


# ===================================================================
# Lifespan — 启动/关闭时管理客户端
# ===================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    if not settings.api_key:
        print("⚠️  未设置 MINIMAX_API_KEY。请复制 .env.example 为 .env 并填入你的 API Key。")
        client = None
    else:
        region_enum = Region.MAINLAND if settings.region == "mainland" else Region.GLOBAL
        client = AsyncMiniMax(
            config=Config(api_key=settings.api_key, region=region_enum)
        )
        print(f"✅ MiniMax 客户端已初始化 (region={settings.region})")
    yield
    if client is not None:
        await client.close()


app = FastAPI(
    title="MiniMax API Standalone Backend",
    description="独立后端，直接调用 MiniMax 所有 API（含流式 TTS/流式 Chat）",
    version="1.0.0",
    lifespan=lifespan,
)

# 挂载静态测试页
TEST_DIR = Path(__file__).resolve().parent / "test"
from fastapi.staticfiles import StaticFiles
if TEST_DIR.exists():
    app.mount("/test", StaticFiles(directory=str(TEST_DIR)), name="test")

# CORS — 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===================================================================
# 1. 文本聊天 — SSE 流式
# ===================================================================
@app.post("/api/v1/text/chat")
async def chat_completions(req: ChatRequest):
    c = get_client()
    if req.stream:
        async def event_stream():
            try:
                async for chunk in c.text.chat_completions_stream(
                    model=req.model,
                    messages=req.messages,
                    temperature=req.temperature,
                    top_p=req.top_p,
                    max_tokens=req.max_tokens,
                ):
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except MiniMaxAPIError as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        result = await c.text.chat_completions(
            model=req.model,
            messages=req.messages,
            temperature=req.temperature,
            top_p=req.top_p,
            max_tokens=req.max_tokens,
        )
        return result


# ===================================================================
# 2. 语音合成 TTS — 同步 & 流式
# ===================================================================
@app.post("/api/v1/speech/synthesize")
async def synthesize_speech(req: TTSRequest):
    """TTS 合成 — 返回音频文件或 SSE 流式音频块"""
    c = get_client()
    voice_setting = {"voice_id": req.voice_id, "speed": req.speed}
    if req.volume is not None:
        voice_setting["volume"] = req.volume
    if req.pitch is not None:
        voice_setting["pitch"] = req.pitch

    audio_setting = {
        "sample_rate": req.sample_rate,
        "bitrate": req.bitrate,
        "format": req.format,
    }

    if req.stream:
        async def audio_stream():
            try:
                async for chunk in c.speech.t2a_stream(
                    model=req.model,
                    text=req.text,
                    voice_setting=voice_setting,
                    audio_setting=audio_setting,
                    subtitle_enable=req.subtitle_enable,
                ):
                    audio_b64 = base64.b64encode(chunk.audio).decode("utf-8")
                    payload = {
                        "audio": audio_b64,
                        "is_final": chunk.is_final,
                        "status": chunk.status,
                        "trace_id": chunk.trace_id,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except MiniMaxAPIError as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            audio_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        result = await c.speech.t2a(
            model=req.model,
            text=req.text,
            voice_setting=voice_setting,
            audio_setting=audio_setting,
            subtitle_enable=req.subtitle_enable,
        )
        file_id = result.file_id
        if file_id:
            audio_data = await c.files.retrieve_content(file_id)
            content_type = f"audio/{req.format}" if req.format != "mp3" else "audio/mpeg"
            return Response(content=audio_data, media_type=content_type)
        return result.raw


@app.post("/api/v1/speech/synthesize-stream/audio")
async def synthesize_speech_audio_stream(req: TTSRequest):
    """TTS 流式 — 直接返回 audio/mpeg 流（逐步拼接音频块）"""
    c = get_client()
    voice_setting = {"voice_id": req.voice_id, "speed": req.speed}
    audio_setting = {
        "sample_rate": req.sample_rate,
        "bitrate": req.bitrate,
        "format": req.format,
    }

    async def raw_audio_stream():
        async for chunk in c.speech.t2a_stream(
            model=req.model,
            text=req.text,
            voice_setting=voice_setting,
            audio_setting=audio_setting,
            subtitle_enable=req.subtitle_enable,
        ):
            yield chunk.audio

    return StreamingResponse(
        raw_audio_stream(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": 'inline; filename="tts_stream.mp3"',
        },
    )


# ── 长文本 TTS ──────────────────────────────────────────────────────
@app.post("/api/v1/speech/long-text")
async def create_long_text(req: TTSRequest):
    c = get_client()
    voice_setting = {"voice_id": req.voice_id, "speed": req.speed}
    audio_setting = {
        "sample_rate": req.sample_rate,
        "bitrate": req.bitrate,
        "format": req.format,
    }
    result = await c.speech.create_long_text(
        model=req.model,
        text=req.text,
        voice_setting=voice_setting,
        audio_setting=audio_setting,
    )
    return result.raw


@app.get("/api/v1/speech/long-text/{task_id}")
async def query_long_text(task_id: str):
    c = get_client()
    result = await c.speech.query_long_text(task_id)
    return result


@app.get("/api/v1/speech/long-text/download/{file_id}")
async def download_long_text(file_id: str):
    c = get_client()
    audio_data = await c.speech.download_long_text(file_id)
    return Response(content=audio_data, media_type="audio/mpeg")


# ===================================================================
# 3. 音色克隆
# ===================================================================
@app.get("/api/v1/voices")
async def list_voices(voice_type: str = "all"):
    c = get_client()
    result = await c.voice.list_voices(voice_type)
    return result


@app.post("/api/v1/voices/design")
async def design_voice(req: VoiceDesignRequest):
    c = get_client()
    result = await c.voice.voice_design(
        prompt=req.prompt,
        preview_text=req.preview_text,
        voice_id=req.voice_id,
    )
    return result.raw


@app.post("/api/v1/voice-clones")
async def create_voice_clone(req: VoiceCloneRequest):
    c = get_client()
    result = await c.voice.voice_clone(
        file_id=req.file_id,
        voice_id=req.voice_id,
        clone_prompt=req.clone_prompt,
    )
    return result.raw


@app.delete("/api/v1/voice-clones/{voice_id}")
async def delete_voice_clone(voice_id: str):
    c = get_client()
    result = await c.request("POST", "/files/delete", json={"file_id": voice_id, "purpose": "voice_clone"})
    return result


# ===================================================================
# 4. 音乐生成
# ===================================================================
@app.post("/api/v1/music/generations")
async def generate_music(req: MusicRequest):
    c = get_client()
    result = await c.music.music_generation(
        model=req.model,
        prompt=req.prompt,
        lyrics=req.lyrics,
    )
    return result.raw


@app.post("/api/v1/music/lyrics")
async def generate_lyrics(req: LyricsRequest):
    c = get_client()
    result = await c.music.lyrics_generation(
        mode=req.mode,
        prompt=req.prompt,
        title=req.title,
    )
    return result.raw


# ===================================================================
# 5. 图像生成
# ===================================================================
@app.post("/api/v1/images/generations")
async def generate_image(req: ImageRequest):
    c = get_client()
    kwargs: Dict[str, Any] = {"model": req.model, "prompt": req.prompt}
    if req.aspect_ratio:
        kwargs["aspect_ratio"] = req.aspect_ratio

    if req.subject_reference:
        result = await c.image.image_to_image(
            model=req.model,
            prompt=req.prompt,
            subject_reference=req.subject_reference,
            aspect_ratio=req.aspect_ratio,
        )
    else:
        result = await c.image.text_to_image(**kwargs)
    return result.raw


# ===================================================================
# 6. 视频生成
# ===================================================================
@app.post("/api/v1/videos/generations")
async def generate_video(req: VideoRequest):
    c = get_client()
    if req.first_frame_image:
        result = await c.video.image_to_video(
            model=req.model,
            prompt=req.prompt,
            first_frame_image=req.first_frame_image,
        )
    else:
        result = await c.video.text_to_video(
            model=req.model,
            prompt=req.prompt,
        )
    return result.raw


@app.get("/api/v1/videos/query/{task_id}")
async def query_video(task_id: str):
    c = get_client()
    result = await c.video.query(task_id)
    return result


# ===================================================================
# 7. 文件管理
# ===================================================================
@app.post("/api/v1/files/upload")
async def upload_file(file: UploadFile = File(...), purpose: str = Form("general")):
    c = get_client()
    temp_path = Path(f"/tmp/minimax_upload_{file.filename}")
    content = await file.read()
    temp_path.write_bytes(content)
    try:
        result = await c.files.upload(str(temp_path), purpose=purpose)
        return result.raw
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/v1/files")
async def list_files(purpose: str = Query("general")):
    c = get_client()
    result = await c.files.list(purpose=purpose)
    return result


@app.get("/api/v1/files/{file_id}")
async def get_file(file_id: str):
    c = get_client()
    result = await c.files.retrieve(file_id)
    return result


@app.get("/api/v1/files/{file_id}/content")
async def get_file_content(file_id: str):
    c = get_client()
    content = await c.files.retrieve_content(file_id)
    # 尝试获取文件名
    try:
        meta = await c.files.retrieve(file_id)
        file_info = meta.get("file", {}) if isinstance(meta.get("file"), dict) else {}
        filename = file_info.get("filename", f"{file_id}")
    except Exception:
        filename = f"{file_id}"
    return Response(content=content, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.delete("/api/v1/files/{file_id}")
async def delete_file(file_id: str, purpose: str = Query("general")):
    c = get_client()
    result = await c.files.delete(file_id, purpose=purpose)
    return result


# ===================================================================
# 8. 任务查询（对视频/长文本等异步任务）
# ===================================================================
@app.get("/api/v1/jobs")
async def list_jobs():
    """列出所有可通过文件系统查询的异步任务（通用端点）"""
    return {"message": "Use /api/v1/videos/query/{task_id} or /api/v1/speech/long-text/{task_id} for specific task types"}


@app.get("/api/v1/jobs/{task_id}")
async def get_job(task_id: str):
    """通用任务查询 — 先试视频，再试长文本 TTS"""
    c = get_client()
    try:
        return await c.video.query(task_id)
    except Exception:
        try:
            return await c.speech.query_long_text(task_id)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found: {e}")


# ===================================================================
# 9. 存储的媒体文件访问（从 MiniMax 下载缓存）
# ===================================================================
MEDIA_CACHE = Path(os.path.dirname(__file__)) / "media_cache"
MEDIA_CACHE.mkdir(exist_ok=True)


@app.get("/api/v1/media/{file_id}")
async def get_media(file_id: str):
    """获取媒体文件（图片/音频），带本地缓存"""
    c = get_client()
    # 检查本地缓存
    cached = MEDIA_CACHE / file_id
    if cached.exists():
        return FileResponse(str(cached))
    # 从 MiniMax 下载
    content = await c.files.retrieve_content(file_id)
    cached.write_bytes(content)
    # 尝试判断类型
    try:
        meta = await c.files.retrieve(file_id)
        file_info = meta.get("file", {}) if isinstance(meta.get("file"), dict) else {}
        filename = file_info.get("filename", "")
    except Exception:
        filename = ""
    ext = Path(filename).suffix.lower() if filename else ""
    media_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".gif": "image/gif", ".webp": "image/webp",
                   ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
                   ".mp4": "video/mp4"}
    content_type = media_types.get(ext, "application/octet-stream")
    return Response(content=content, media_type=content_type)


# ===================================================================
# 9.5 通用请求端点（直接透传任意 MiniMax API 调用）
# ===================================================================
class GenericRequest(BaseModel):
    method: str = "POST"
    path: str
    body_json: Optional[Dict[str, Any]] = Field(None, alias="json")
    params: Optional[Dict[str, Any]] = None


@app.post("/api/v1/request")
async def generic_request(req: GenericRequest):
    c = get_client()
    try:
        result = await c.request(
            method=req.method.upper(),
            path=req.path,
            json=req.body_json,
            params=req.params,
        )
        return result
    except MiniMaxAPIError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===================================================================
# 10. 主页 — API 测试导航
# ===================================================================
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(INDEX_HTML)


# ===================================================================
# 启动
# ===================================================================
if __name__ == "__main__":
    if not settings.api_key:
        print("=" * 60)
        print("  ⚠️  未检测到 MINIMAX_API_KEY")
        print("  请复制 .env.example 为 .env 并填入你的 API Key")
        print("  API Key 获取地址: https://platform.minimaxi.com")
        print("=" * 60)
    print(f"🚀 启动 MiniMax API 后端于 http://0.0.0.0:{settings.port}")
    print(f"📖 API 文档: http://localhost:{settings.port}/docs")
    print(f"🧪 测试页:   http://localhost:{settings.port}")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=True)


# ===================================================================
# 内嵌 Dashboard HTML
# ===================================================================
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MiniMax API 测试平台</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f5f5f7; color: #1d1d1f; min-height: 100vh; }
  .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #fff; padding: 48px 24px; text-align: center; }
  .header h1 { font-size: 2.2rem; font-weight: 700; margin-bottom: 8px; }
  .header p { opacity: 0.9; font-size: 1.05rem; }
  .container { max-width: 1200px; margin: 0 auto; padding: 32px 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
          gap: 20px; }
  .card { background: #fff; border-radius: 16px; padding: 24px; box-shadow: 0 2px 12px rgba(0,0,0,0.08);
          transition: transform 0.2s, box-shadow 0.2s; cursor: pointer;
          text-decoration: none; color: inherit; display: block; }
  .card:hover { transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0,0,0,0.12); }
  .card-icon { font-size: 2.4rem; margin-bottom: 12px; }
  .card h3 { font-size: 1.15rem; margin-bottom: 6px; }
  .card p { font-size: 0.85rem; color: #86868b; line-height: 1.5; }
  .badge { display: inline-block; background: #e8f5e9; color: #2e7d32; font-size: 0.7rem;
           padding: 2px 8px; border-radius: 10px; margin-top: 8px; font-weight: 500; }
  .badge.stream { background: #e3f2fd; color: #1565c0; }
  .badge.async { background: #fff3e0; color: #e65100; }
  .footer { text-align: center; padding: 24px; color: #86868b; font-size: 0.85rem; }
  .api-info { background: #fff; border-radius: 12px; padding: 16px 20px; margin-top: 24px;
              box-shadow: 0 1px 6px rgba(0,0,0,0.06); }
  .api-info h3 { font-size: 1rem; margin-bottom: 8px; }
  .api-info code { background: #f0f0f0; padding: 2px 6px; border-radius: 4px;
                   font-size: 0.85rem; }
  .api-info a { color: #667eea; text-decoration: none; }
  .api-info a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="header">
  <h1>🎛️ MiniMax API 测试平台</h1>
  <p>独立后端 · 直接调用 MiniMax 全能力 · 流式/同步全支持</p>
</div>
<div class="container">
  <div class="grid">
    <a href="/test/chat.html" class="card">
      <div class="card-icon">💬</div>
      <h3>文本聊天</h3>
      <p>SSE 流式对话 · 支持 MiniMax-M3/M2.5 模型</p>
      <span class="badge stream">流式 SSE</span>
    </a>
    <a href="/test/tts.html" class="card">
      <div class="card-icon">🔊</div>
      <h3>TTS 语音合成</h3>
      <p>文本转语音 · 流式输出 · 多种音色/参数调节</p>
      <span class="badge stream">流式</span>
      <span class="badge">同步</span>
    </a>
    <a href="/test/music.html" class="card">
      <div class="card-icon">🎵</div>
      <h3>音乐生成</h3>
      <p>AI 音乐创作 · 歌词生成 · 音乐翻唱</p>
      <span class="badge">同步</span>
    </a>
    <a href="/test/image.html" class="card">
      <div class="card-icon">🖼️</div>
      <h3>图像生成</h3>
      <p>文生图 · 图生图 · 多种尺寸比例</p>
      <span class="badge">同步</span>
    </a>
    <a href="/test/video.html" class="card">
      <div class="card-icon">🎬</div>
      <h3>视频生成</h3>
      <p>文生视频 · 图生视频 · 任务轮询查询</p>
      <span class="badge async">异步</span>
    </a>
    <a href="/test/voice.html" class="card">
      <div class="card-icon">🎤</div>
      <h3>音色克隆</h3>
      <p>上传音频样本 · 快速复刻 · 音色设计</p>
      <span class="badge">同步</span>
    </a>
    <a href="/test/files.html" class="card">
      <div class="card-icon">📁</div>
      <h3>文件管理</h3>
      <p>上传/查看/下载/删除 MiniMax 文件</p>
      <span class="badge">管理</span>
    </a>
    <a href="/test/jobs.html" class="card">
      <div class="card-icon">📋</div>
      <h3>任务管理</h3>
      <p>查询异步任务状态 · 任务列表</p>
      <span class="badge async">任务</span>
    </a>
  </div>

  <div class="api-info">
    <h3>🔗 API 文档</h3>
    <p>
      <code>GET <a href="/docs" target="_blank">/docs</a></code> Swagger UI 交互式文档 ·
      <code>GET <a href="/redoc" target="_blank">/redoc</a></code> ReDoc 文档
    </p>
  </div>
</div>
<div class="footer">
  MiniMax API Standalone Backend v1.0 · 基于 FastAPI + MiniMax Python SDK
</div>
</body>
</html>"""
