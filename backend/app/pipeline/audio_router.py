"""
音频生成路由 - /api/audio/*

直接暴露 audio_tools / voice_clone_tools 工具为 REST 端点，
前端可独立调用，也可通过 Agent 意图触发。

端点：
  POST /api/audio/suno                    - Suno AI 生成歌曲
  POST /api/audio/minimax                 - MiniMax 生成歌曲
  POST /api/audio/minimax/cover           - MiniMax 翻唱
  POST /api/audio/minimax/lyrics          - MiniMax 生成歌词
  POST /api/audio/prompt-from-abc         - ABC 谱转 prompt（辅助）
  POST /api/audio/voice/upload-sample     - 上传音色克隆源音频
  POST /api/audio/voice/upload-prompt     - 上传增强样本（可选）
  POST /api/audio/voice/clone             - 克隆音色
  GET  /api/audio/voice/list              - 查询已克隆音色列表
  POST /api/audio/voice/synthesize        - 用克隆音色合成语音
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

# 触发工具注册
import app.agentcore.tools.audio_tools        # noqa: F401
import app.agentcore.tools.voice_clone_tools  # noqa: F401
from app.agentcore.tools import call_tool

router = APIRouter(prefix="/api/audio", tags=["audio"])


# ─── 请求模型 ─────────────────────────────────────────────────────────────────

class SunoRequest(BaseModel):
    prompt: str = Field(..., description="歌曲描述或歌词内容")
    style: str = Field("", description="风格标签，逗号分隔，如 'synthwave, 女声'")
    title: str = Field("", description="歌曲标题")
    lyrics: str = Field("", description="自定义歌词（含 [Verse]/[Chorus] 标签）")
    instrumental: bool = Field(False, description="是否生成纯器乐")
    model: str = Field("chirp-v5", description="Suno 模型版本")


class MinimaxRequest(BaseModel):
    prompt: str = Field(..., description="音乐风格描述，如 'Indie folk, melancholic'")
    lyrics: str = Field("", description="歌词文本，留空则自动生成")
    instrumental: bool = Field(False, description="是否生成纯音乐")
    model: str = Field("music-2.6", description="music-2.6 或 music-2.6-free")
    output_format: str = Field("url", description="url 或 hex")


class MinimaxCoverRequest(BaseModel):
    audio_url: str = Field(..., description="原始音频 URL（mp3/wav/flac，6s-6min，≤50MB）")
    prompt: str = Field(..., description="翻唱风格描述（10-300字符）")
    lyrics: str = Field("", description="替换歌词（留空保留原歌词）")
    model: str = Field("music-cover", description="music-cover 或 music-cover-free")


class MinimaxLyricsRequest(BaseModel):
    prompt: str = Field(..., description="歌曲主题，如 '一首关于夏夜星空的流行歌曲'")


class AbcToPromptRequest(BaseModel):
    abc: str = Field(..., description="ABC Notation 谱子文本")
    style_hint: str = Field("", description="额外风格提示，如 '中国风' '电子'")


# ─── 路由处理 ─────────────────────────────────────────────────────────────────

@router.post("/suno")
async def generate_suno(req: SunoRequest):
    """Suno AI 生成原创歌曲（异步轮询，最多等 5 分钟）"""
    result = await call_tool("generate_audio_suno", {
        "prompt": req.prompt,
        "style": req.style,
        "title": req.title,
        "lyrics": req.lyrics,
        "instrumental": req.instrumental,
        "model": req.model,
    })
    return result


@router.post("/minimax")
async def generate_minimax(req: MinimaxRequest):
    """MiniMax music-2.6 生成原创歌曲（同步，直接返回）"""
    result = await call_tool("generate_audio_minimax", {
        "prompt": req.prompt,
        "lyrics": req.lyrics,
        "instrumental": req.instrumental,
        "model": req.model,
        "output_format": req.output_format,
    })
    return result


@router.post("/minimax/cover")
async def generate_minimax_cover(req: MinimaxCoverRequest):
    """MiniMax music-cover AI 翻唱"""
    result = await call_tool("generate_cover_minimax", {
        "audio_url": req.audio_url,
        "prompt": req.prompt,
        "lyrics": req.lyrics,
        "model": req.model,
    })
    return result


@router.post("/minimax/lyrics")
async def generate_minimax_lyrics(req: MinimaxLyricsRequest):
    """MiniMax 根据主题生成完整歌词"""
    result = await call_tool("generate_lyrics_minimax", {
        "prompt": req.prompt,
    })
    return result


@router.post("/prompt-from-abc")
async def abc_to_prompt(req: AbcToPromptRequest):
    """将 ABC 谱元信息提取为音频生成 prompt"""
    result = await call_tool("abc_to_audio_prompt", {
        "abc": req.abc,
        "style_hint": req.style_hint,
    })
    return result


# ─── 音色克隆端点 ─────────────────────────────────────────────────────────────

class VoiceUploadRequest(BaseModel):
    audio_b64: str = Field(..., description="音频文件的 base64 编码（mp3/m4a/wav）")
    filename: str = Field("sample.mp3", description="文件名，需带扩展名")


class VoiceCloneRequest(BaseModel):
    file_id: str = Field(..., description="upload_voice_sample 返回的 file_id")
    voice_id: str = Field(..., description="自定义音色 ID（首字母英文，8-256位）")
    prompt_file_id: str = Field("", description="增强样本 file_id（可选）")
    prompt_text: str = Field("", description="增强样本对应文本（可选）")
    preview_text: str = Field("", description="克隆后试听文本（留空不生成试听）")
    need_noise_reduction: bool = Field(False, description="是否降噪")
    need_volume_normalization: bool = Field(False, description="是否音量归一化")


class VoiceListRequest(BaseModel):
    voice_type: str = Field("voice_cloning", description="voice_cloning | system | all")


class VoiceSynthesizeRequest(BaseModel):
    text: str = Field(..., description="要合成的文本（≤10000字符）")
    voice_id: str = Field(..., description="克隆音色 ID 或系统音色 ID")
    model: str = Field("speech-2.6-hd", description="speech-2.6-hd | speech-2.6-turbo | speech-2.8-hd")
    speed: float = Field(1.0, description="语速 [0.5, 2.0]")
    vol: float = Field(1.0, description="音量 [0.1, 10.0]")
    pitch: int = Field(0, description="音调 [-12, 12]")
    output_format: str = Field("url", description="url（链接，有效24h）| hex（base64）")


@router.post("/voice/upload-sample")
async def upload_voice_sample(req: VoiceUploadRequest):
    """上传音色克隆源音频（base64），获取 file_id"""
    return await call_tool("upload_voice_sample", {
        "audio_b64": req.audio_b64,
        "filename": req.filename,
    })


@router.post("/voice/upload-prompt")
async def upload_prompt_audio(req: VoiceUploadRequest):
    """上传增强样本（可选，<8s，提升克隆相似度），获取 file_id"""
    return await call_tool("upload_prompt_audio", {
        "audio_b64": req.audio_b64,
        "filename": req.filename,
    })


@router.post("/voice/clone")
async def clone_voice(req: VoiceCloneRequest):
    """基于已上传音频克隆音色，生成可复用的 voice_id（7天内未使用自动删除）"""
    return await call_tool("clone_voice_minimax", {
        "file_id": req.file_id,
        "voice_id": req.voice_id,
        "prompt_file_id": req.prompt_file_id,
        "prompt_text": req.prompt_text,
        "preview_text": req.preview_text,
        "need_noise_reduction": req.need_noise_reduction,
        "need_volume_normalization": req.need_volume_normalization,
    })


@router.post("/voice/list")
async def list_voices(req: VoiceListRequest):
    """查询账号下已克隆的音色列表"""
    return await call_tool("list_cloned_voices", {
        "voice_type": req.voice_type,
    })


@router.post("/voice/synthesize")
async def synthesize_speech(req: VoiceSynthesizeRequest):
    """使用克隆音色（或系统音色）将文本合成为语音"""
    return await call_tool("synthesize_speech_minimax", {
        "text": req.text,
        "voice_id": req.voice_id,
        "model": req.model,
        "speed": req.speed,
        "vol": req.vol,
        "pitch": req.pitch,
        "output_format": req.output_format,
    })
