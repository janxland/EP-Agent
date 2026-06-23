// API 服务层 - 对应后端 REST 接口
// 学习 magic-coding 的 features/chat/services/chat.service.ts 模式

import type {
  Score,
  ScoreMeta,
  EditResult,
  ExportFormat,
  SSEEvent,
  AudioGenerationRequest,
  AudioGenerationResult,
} from '@/shared/types'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8082'

// ─── Session ──────────────────────────────────────────────────

export async function createSession(): Promise<{ session_id: string }> {
  const res = await fetch(`${BASE_URL}/api/sessions`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ─── Convert: JSON → ABC ──────────────────────────────────────

export interface ConvertResponse {
  session_id: string
  score_id: string
  abc_notation: string
  meta: ScoreMeta
}

export async function convertJSON(
  sessionId: string,
  jsonContent: string,
  fileName: string
): Promise<ConvertResponse> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/convert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ json_content: jsonContent, file_name: fileName }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

// ─── Edit: 意图驱动修改 ───────────────────────────────────────

export async function editABC(
  sessionId: string,
  intent: string
): Promise<EditResult> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ intent }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

// ─── Export ───────────────────────────────────────────────────

export async function exportScore(
  sessionId: string,
  format: ExportFormat,
  instrument = 0
): Promise<Blob> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format, instrument }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.blob()
}

// ─── SSE Stream ───────────────────────────────────────────────

export function subscribeToSession(
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onError?: (err: Event) => void
): () => void {
  const url = `${BASE_URL}/api/sessions/${sessionId}/stream`
  const es = new EventSource(url)

  es.onmessage = (e) => {
    try {
      const event: SSEEvent = JSON.parse(e.data)
      onEvent(event)
    } catch {
      // 忽略解析错误
    }
  }

  if (onError) {
    es.onerror = onError
  }

  // 返回取消订阅函数
  return () => es.close()
}

// ─── Audio Generation ─────────────────────────────────────────

export async function generateAudioSuno(
  params: Pick<AudioGenerationRequest, 'prompt' | 'style' | 'lyrics' | 'title' | 'instrumental'>
): Promise<AudioGenerationResult> {
  const res = await fetch(`${BASE_URL}/api/audio/suno`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

export async function generateAudioMinimax(
  params: Pick<AudioGenerationRequest, 'prompt' | 'lyrics' | 'instrumental'> & { model?: string }
): Promise<AudioGenerationResult> {
  const res = await fetch(`${BASE_URL}/api/audio/minimax`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

export async function generateCoverMinimax(
  params: { audio_url: string; prompt: string; lyrics?: string }
): Promise<AudioGenerationResult> {
  const res = await fetch(`${BASE_URL}/api/audio/minimax/cover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

export async function generateLyricsMinimax(prompt: string): Promise<{ lyrics: string; title: string }> {
  const res = await fetch(`${BASE_URL}/api/audio/minimax/lyrics`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

// ─── Audio Chat（对话式音频生成）────────────────────────────────────────────

import type { AudioChatRequest, AudioChatResponse, AudioTurn } from '@/shared/types'

/**
 * 对话式音频生成 - 核心 API
 * 首次调用生成新音频，后续调用在上次基础上迭代（"再欢快一点"式交互）
 */
export async function chatAudio(
  sessionId: string,
  message: string,
  provider: 'auto' | 'minimax' | 'suno' = 'auto',
  audioB64?: string
): Promise<AudioChatResponse> {
  const body: AudioChatRequest = { message, provider, ...(audioB64 ? { audio_b64: audioB64 } : {}) }
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/audio/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json()
}

/**
 * 获取音频对话历史
 */
export async function getAudioHistory(sessionId: string): Promise<{
  session_id: string
  total_turns: number
  history: AudioTurn[]
}> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/audio/history`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

/**
 * 清空音频对话历史（重新开始）
 */
export async function clearAudioHistory(sessionId: string): Promise<void> {
  await fetch(`${BASE_URL}/api/sessions/${sessionId}/audio/history`, {
    method: 'DELETE',
  })
}

// ─── Voice Clone（音色克隆）──────────────────────────────────────────────────

export interface VoiceUploadResult {
  file_id: string
  bytes: number
  filename: string
  purpose: string
  error?: string
}

export interface VoiceCloneResult {
  voice_id: string
  demo_audio: string   // 试听音频 URL（提供 preview_text 时有值）
  status: 'success' | 'failed'
  message: string
  error?: string
}

export interface VoiceItem {
  voice_id: string
  name: string
  type: string
}

export interface VoiceListResult {
  voices: VoiceItem[]
  total: number
  error?: string
}

export interface VoiceSynthesizeResult {
  audio_url: string
  audio_b64: string
  duration_ms: number
  usage_characters: number
  voice_id: string
  model: string
  provider: string
  error?: string
}

/** 上传音色克隆源音频（base64），获取 file_id */
export async function uploadVoiceSample(
  audioB64: string,
  filename = 'sample.mp3'
): Promise<VoiceUploadResult> {
  const res = await fetch(`${BASE_URL}/api/audio/voice/upload-sample`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio_b64: audioB64, filename }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

/** 上传增强样本（可选，<8s，提升克隆相似度） */
export async function uploadPromptAudio(
  audioB64: string,
  filename = 'prompt.mp3'
): Promise<VoiceUploadResult> {
  const res = await fetch(`${BASE_URL}/api/audio/voice/upload-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio_b64: audioB64, filename }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

/** 基于已上传音频克隆音色 */
export async function cloneVoice(params: {
  file_id: string
  voice_id: string
  prompt_file_id?: string
  prompt_text?: string
  preview_text?: string
  need_noise_reduction?: boolean
  need_volume_normalization?: boolean
}): Promise<VoiceCloneResult> {
  const res = await fetch(`${BASE_URL}/api/audio/voice/clone`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

/** 查询账号下已克隆的音色列表 */
export async function listClonedVoices(
  voiceType: 'voice_cloning' | 'system' | 'all' = 'voice_cloning'
): Promise<VoiceListResult> {
  const res = await fetch(`${BASE_URL}/api/audio/voice/list`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice_type: voiceType }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}

/** 使用克隆音色（或系统音色）将文本合成为语音 */
export async function synthesizeSpeech(params: {
  text: string
  voice_id: string
  model?: string
  speed?: number
  vol?: number
  pitch?: number
  output_format?: 'url' | 'hex'
}): Promise<VoiceSynthesizeResult> {
  const res = await fetch(`${BASE_URL}/api/audio/voice/synthesize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error ?? res.statusText)
  }
  return res.json()
}
