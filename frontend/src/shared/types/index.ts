// 共享类型定义
// 对应后端 pipeline/domain 领域模型

// ─── Score ────────────────────────────────────────────────────

export interface ScoreMeta {
  title: string
  composer: string
  arranged_by?: string
  transcribed_by?: string
  bpm: number
  raw_bpm?: number
  key: string
  /** 嵌套格式（前端 API 响应使用） */
  time_sig?: { num: number; den: number }
  /** 扁平格式（session/store.ts 内部使用，与后端 DB 字段对齐） */
  time_sig_num?: number
  time_sig_den?: number
  note_count: number
  pitch_level: number
  duration_ms?: number
}

export interface Score {
  id: string
  title: string
  abc_notation: string
  meta: ScoreMeta
  version: number
}

// ─── Session ──────────────────────────────────────────────────

export type PipelineState = 'idle' | 'running' | 'succeeded' | 'failed'

export interface Session {
  session_id: string
  pipeline_state: PipelineState
  score: Score | null
}

// ─── Intent ───────────────────────────────────────────────────

export type IntentType = 'transpose' | 'tempo' | 'style' | 'structure' | 'custom'

export interface ToolCallRecord {
  id: string
  tool: string
  arguments: Record<string, unknown>
  result_preview?: string
  status: 'running' | 'succeeded' | 'failed'
  error?: string
}

export interface EditResult {
  session_id: string
  abc_notation: string
  intent_type: IntentType
  summary: string
  version: number
  tool_calls: ToolCallRecord[]
  sky_json?: string    // scene=player/raw
  midi_b64?: string   // scene=daw/raw
}

// ─── Audio Generation ───────────────────────────────────────

export type AudioProvider = 'suno' | 'minimax'

export interface AudioGenerationRequest {
  provider: AudioProvider
  prompt: string
  style?: string
  lyrics?: string
  title?: string
  instrumental?: boolean
  // MiniMax cover 模式
  cover_audio_url?: string
}

export interface AudioGenerationResult {
  provider: AudioProvider
  audio_url: string
  audio_b64?: string
  duration_ms?: number
  duration?: number
  music_id?: string
  job_id?: string
  title?: string
  error?: string
}

// ─── Audio Chat（对话式音频生成）────────────────────────────────

/** 单轮音频对话记录 */
export interface AudioTurn {
  turn: number
  user_message: string
  domain: string               // audio_generate | audio_iterate | audio_cover
  prompt: string
  style: string
  lyrics: string
  instrumental: boolean
  provider: string
  model: string
  audio_url: string
  audio_b64: string
  duration_ms: number
  summary: string
  suggestions: string[]
  diff_summary: string         // 与上轮的差异说明（迭代时有值）
  tool_calls?: ToolCallRecord[]
  // voice_clone 域专属
  voice_id?: string            // 克隆或使用的音色 ID
  demo_audio?: string          // 克隆后试听音频 URL
}

// ─── Audio Domain 枚举（统一区分渲染逻辑，替代散落的字符串比较）──────────────
export const AudioDomain = {
  GENERATE: 'audio_generate',
  ITERATE:  'audio_iterate',
  COVER:    'audio_cover',
  CLONE:    'voice_clone',
} as const
export type AudioDomainValue = typeof AudioDomain[keyof typeof AudioDomain]

/** 对话式音频生成请求 */
export interface AudioChatRequest {
  message: string
  provider?: 'auto' | 'minimax' | 'suno'
  audio_b64?: string   // 音色克隆时携带的源音频 base64
}

/** 对话式音频生成响应 */
export type AudioChatResponse = AudioTurn

// ─── Export ───────────────────────────────────────────────────

export type ExportFormat = 'abc' | 'midi' | 'json'

// ─── SSE Events ───────────────────────────────────────────────

export type SSEEventType =
  | 'connected'
  | 'pipeline.step'
  | 'abc.updated'
  | 'activity.update'
  | 'message.delta'
  | 'message.completed'
  | 'message.history'        // replay：刷新后后端推送的历史对话消息
  | 'tool.call'
  | 'todo.list'
  | 'todo.update'
  | 'todo.append'
  | 'role.active'             // 角色激活（切换角色/刷新恢复/降级后补推）
  | 'h5.ready'               // H5 海报生成完成（含 url_path/file_path/size_kb）
  | 'connection.reconnecting' // SSE 断线重连前通知前端清空 store
  | 'error'

export interface SSEEvent {
  id: string
  type: SSEEventType
  session_id: string
  display: boolean
  sequence: number
  timestamp: string
  payload: Record<string, unknown>
}

// pipeline.step payload
export interface PipelineStepPayload {
  step: string
  status: 'running' | 'succeeded' | 'failed'
  text: string
  note_count?: number
  bpm?: number
  key?: string
  intent_type?: IntentType
  version?: number
}

// abc.updated payload
export interface ABCUpdatedPayload {
  abc: string
  version: number
  summary?: string
}

// message.delta payload（预留：后端实现流式文本时使用）
export interface MessageDeltaPayload {
  delta: string
}

// error payload
export interface ErrorPayload {
  code: string
  message: string
}

// tool.call payload
export interface ToolCallPayload {
  call_id: string          // tc["id"]，用于前端 O(1) 精确匹配 log 条目
  tool: string
  arguments?: Record<string, unknown>
  status: 'running' | 'succeeded' | 'failed'
  result_preview?: string
  error?: string
}
