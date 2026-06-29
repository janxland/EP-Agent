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

// 优先使用环境变量；开发时留空走 next.config.js 代理，生产时由 NEXT_PUBLIC_API_URL 指定
const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ─── Models ──────────────────────────────────────────────────────────────────

export interface ModelItem {
  id: string
  name: string
  group: string
  desc: string
  current?: boolean
}

export async function listModels(): Promise<{ models: ModelItem[]; active: string }> {
  const res = await fetch(`${BASE_URL}/api/models`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function setActiveModel(modelId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/models/active`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model_id: modelId }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function getContextUsage(sessionId: string): Promise<{
  session_id: string; msg_count: number; total_chars: number
  est_tokens: number; ctx_limit: number; pct: number
}> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/context`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ─── Workspace / Project / Session 三层架构 ──────────────────
//
// 层级关系：Workspace（固定容器）→ Project（文件隔离边界）→ Session/Topic（对话上下文）
// - Workspace 是机械的固定容器，Agent 不感知其 ID
// - Project 拥有独立文件目录，Session 只能操作所属 Project 的文件
// - Session（Topic）是上下文隔离单位，可无数个话题操作同一个 Project

export interface SessionInfoDto {
  id: string
  workspace_id: string | null
  project_id: string | null
  title: string
  score_title: string | null
  score_key: string | null
  score_bpm: number | null
  score_notes: number | null
  pipeline_state: string
  created_at: string
  updated_at: string
  stale?: boolean
}

export interface ProjectDto {
  id: string
  workspace_id: string
  name: string
  description: string
  created_at: string
  updated_at: string
  sessions?: SessionInfoDto[]
}

export interface WorkspaceDto {
  id: string
  name: string
  description: string
  created_at: string
  updated_at: string
  projects?: ProjectDto[]       // 三层结构：项目列表（含嵌套 sessions）
  sessions?: SessionInfoDto[]   // 向后兼容：该工作区下所有 sessions 的扁平列表
}

export async function listWorkspaces(): Promise<{ workspaces: WorkspaceDto[] }> {
  const res = await fetch(`${BASE_URL}/api/workspaces`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createWorkspace(name: string, description = ''): Promise<WorkspaceDto> {
  const res = await fetch(`${BASE_URL}/api/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function renameWorkspace(wsId: string, name: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/workspaces/${wsId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function deleteWorkspace(wsId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/workspaces/${wsId}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 404) throw new Error(await res.text())
}

// ─── Project API（三层架构：Workspace → Project → Session）──────────────────

export async function listProjects(wsId: string): Promise<{ workspace_id: string; projects: ProjectDto[] }> {
  const res = await fetch(`${BASE_URL}/api/workspaces/${wsId}/projects`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createProject(wsId: string, name: string, description = ''): Promise<ProjectDto> {
  const res = await fetch(`${BASE_URL}/api/workspaces/${wsId}/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function renameProject(projId: string, name: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/projects/${projId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function deleteProject(projId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/projects/${projId}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 404) throw new Error(await res.text())
}

export async function getProjectInfo(projId: string): Promise<ProjectDto> {
  const res = await fetch(`${BASE_URL}/api/projects/${projId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ─── Session ──────────────────────────────────────────────────

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 404) throw new Error(await res.text())
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function getSessionInfo(sessionId: string): Promise<SessionInfoDto> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

/**
 * 创建新对话（Session/Topic）
 * projectId 为空时后端自动关联工作区的默认项目（或自动创建）
 */
export async function createSession(
  workspaceId?: string,
  title = '新对话',
  projectId?: string,
): Promise<{ session_id: string; workspace_id: string | null; project_id: string | null; title: string }> {
  const res = await fetch(`${BASE_URL}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workspace_id: workspaceId ?? '',
      project_id:   projectId   ?? '',
      title,
    }),
  })
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

/**
 * 订阅 SSE 事件流，含自动重连机制。
 *
 * 关键设计（解决前端时序堵点）：
 *   1. 断线自动重连：网络抖动或后端重启后，3s 内自动重建连接
 *   2. 重连时后端会重新 replay abc.updated / message.history / todo.list，
 *      前端去重逻辑确保不会重复显示历史消息
 *   3. 主动销毁时（组件卸载）不再重连
 *
 * ⚠️ SSE 必须直连后端，不能走 Next.js rewrites 代理。
 *    Next.js 的 rewrites 会缓冲整个响应后才转发，导致流式事件无法实时到达。
 *    生产环境通过 NEXT_PUBLIC_BACKEND_URL 指定后端地址（nginx 反代时填内网地址）。
 */
export function subscribeToSession(
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onError?: (err: Event) => void
): () => void {
  // SSE 直连后端，绕过 Next.js rewrites 缓冲
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL ?? 'http://localhost:8080'
  const url = `${backendUrl}/api/sessions/${sessionId}/stream`
  let es: EventSource | null = null
  let destroyed = false
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  const MAX_RETRY = 10
  const RETRY_BASE_MS = 2000   // 基础重连间隔 2s
  const RETRY_MAX_MS  = 30000  // 最大重连间隔 30s

  function connect() {
    if (destroyed) return
    es = new EventSource(url)

    es.onmessage = (e) => {
      retryCount = 0   // 收到消息说明连接正常，重置重试计数
      try {
        const event: SSEEvent = JSON.parse(e.data)
        onEvent(event)
      } catch {
        // 忽略解析错误（心跳注释行等非 JSON 数据）
      }
    }

    es.onerror = (err) => {
      if (onError) onError(err)
      es?.close()
      es = null
      if (destroyed || retryCount >= MAX_RETRY) return
      // 首次断线重连前：通知前端清空 store，避免 SSE replay 与已有历史消息重复
      // 后续重连（retryCount > 0）不再重复通知，防止多次清空
      if (retryCount === 0) {
        try {
          onEvent({
            id: 'reconnecting',
            type: 'connection.reconnecting',
            session_id: sessionId,
            display: false,
            sequence: -1,
            timestamp: new Date().toISOString(),
            payload: {},
          })
        } catch {
          // 忽略：前端若未处理此事件类型不影响重连逻辑
        }
      }
      // 指数退避重连（2s → 4s → 8s → … → 30s）
      const delay = Math.min(RETRY_BASE_MS * Math.pow(1.5, retryCount), RETRY_MAX_MS)
      retryCount++
      retryTimer = setTimeout(connect, delay)
    }
  }

  connect()

  // 返回取消订阅函数（组件卸载时调用）
  return () => {
    destroyed = true
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
    es?.close()
    es = null
  }
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

// ─── Session History（历史消息/TODO 查询）──────────────────────────────────────

export interface HistoryMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls: string | null
  tool_call_id: string | null
  tool_name: string | null
  created_at: string
}

export interface HistoryTodo {
  id: string
  session_id: string
  title: string
  detail: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'  // skipped: finish_gate 跳过的未执行步骤
  domain: string
  summary: string
  created_at: string
}

export async function getSessionMessages(sessionId: string): Promise<{ session_id: string; messages: HistoryMessage[] }> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/messages`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getSessionTodos(sessionId: string): Promise<{ session_id: string; todos: HistoryTodo[] }> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/todos`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

// ─── Universal Chat（统一对话接口）──────────────────────────────────────────

export interface UniversalChatRequest {
  message: string
  workspace_id?: string                // 工作区 ID（必带，与 project_id 共同定位文件系统路径）
  project_id?: string                  // 项目 ID（必带，工具层文件隔离边界依赖此字段）
  attachment_content?: string          // 文本附件内容（ABC/JSON/TXT，可进 LLM context）
  attachment_name?: string             // 附件文件名
  attachment_workspace_path?: string   // 工作区相对路径（MIDI/图片/音频，后端 Runner 层处理，不传 base64）
  attachment_b64?: string              // 音频 base64（仅音色克隆直接上传场景，其余留空）
}

export interface UniversalChatResponse {
  /** 后端实际返回 202 Accepted，结果全部通过 SSE 推送 */
  status: string                // "accepted"
  session_id: string
}

/**
 * 统一对话接口：LLM 自动识别意图，无需前端区分场景。
 * - 粘贴 Sky JSON → domain=convert（自动转换）
 * - 说"升高八度" → domain=edit（直接修改 ABC）
 * - 说"生成中国风" → domain=audio（音频生成）
 * - 说"克隆声音"+音频 → domain=voice（音色克隆）
 * - 问"这首是什么调" → domain=query（直接回答）
 */
export async function chatUniversal(
  sessionId: string,
  req: UniversalChatRequest,
): Promise<UniversalChatResponse> {
  const res = await fetch(`${BASE_URL}/api/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail ?? res.statusText)
  }
  return res.json()
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
