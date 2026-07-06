// 审计与重播系统类型定义（对应后端 traces / spans / replay_fixtures 表）
// Phase 1: 审计链路可视化

// ─── Trace（一次完整执行）────────────────────────────────────────

export type TraceStatus = 'running' | 'succeeded' | 'failed' | 'aborted'

export interface TraceDto {
  trace_id: string
  session_id: string
  domain: string              // sovits | voice | audio | edit | query | ...
  role_id: string
  user_message: string        // 截断至 500 字符
  attachment_name: string
  started_at: string
  ended_at: string
  duration_ms: number
  status: TraceStatus
  total_steps: number
  input_tokens: number
  output_tokens: number
}

// ─── Span（单个工具调用 / 推理步骤）─────────────────────────────────

export type SpanKind = 'model' | 'tool' | 'routing' | 'todo_plan' | 'memory' | 'chain'
export type SpanStatus = 'running' | 'ok' | 'error' | 'timeout' | 'skipped'

export interface SpanDto {
  span_id: string
  trace_id: string
  parent_span_id: string
  agent_name: string
  span_kind: SpanKind
  round_idx: number           // ReAct 第几轮（0-based）
  step_idx: number            // 全局步骤序号
  // 工具调用专属
  tool_name: string
  tool_args: string           // JSON string，截断至 4096 字符
  tool_args_hash: string      // SHA256[:16]，用于 fixture 匹配
  tool_result: string         // JSON string，截断至 4096 字符
  tool_result_preview: string // 截断至 200 字符，前端展示用
  attempt: number
  // 模型调用专属
  model: string
  temperature: number
  input_tokens: number
  output_tokens: number
  finish_reason: string
  // 通用
  started_at: string
  ended_at: string
  duration_ms: number
  status: SpanStatus
  error_msg: string
  call_id: string
}

// ─── ReplayFixture（重播快照）────────────────────────────────────

export interface FixtureDto {
  fixture_id: string
  trace_id: string
  span_id: string
  tool_name: string
  tool_args_hash: string
  tool_result: string
}

// ─── ReplaySession（重播会话）────────────────────────────────────

export type ReplayMode = 'fixture' | 'live'
export type ReplayStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface ReplayDto {
  replay_id: string
  source_trace_id: string
  replay_trace_id: string
  session_id: string
  mode: ReplayMode
  status: ReplayStatus
  diff_summary: string
  created_at: string
  updated_at: string
}

// ─── API 响应类型 ─────────────────────────────────────────────────

export interface ListTracesResponse {
  ok: boolean
  traces: TraceDto[]
}

export interface GetTraceDetailResponse {
  ok: boolean
  trace: TraceDto
  spans: SpanDto[]
}

export interface ListSpansResponse {
  ok: boolean
  spans: SpanDto[]
}

export interface ListFixturesResponse {
  ok: boolean
  fixtures: FixtureDto[]
}

// ─── 前端 UI 辅助类型 ────────────────────────────────────────────

/** span_kind 对应的显示颜色 */
export const SPAN_KIND_COLOR: Record<SpanKind, string> = {
  tool:      'bg-blue-100 text-blue-700',
  model:     'bg-purple-100 text-purple-700',
  routing:   'bg-orange-100 text-orange-700',
  todo_plan: 'bg-yellow-100 text-yellow-700',
  memory:    'bg-green-100 text-green-700',
  chain:     'bg-pink-100 text-pink-700',
}

/** span status 对应的图标 */
export const SPAN_STATUS_ICON: Record<SpanStatus, string> = {
  ok:      '✓',
  running: '⟳',
  error:   '✗',
  timeout: '⏱',
  skipped: '–',
}

/** domain 对应的中文标签（静态兜底，优先使用 getDomainLabel() 动态获取）*/
export const DOMAIN_LABEL: Record<string, string> = {
  sovits:  'GPT-SoVITS 克隆',
  voice:   'MiniMax 音色',
  audio:   '音频生成',
  edit:    'ABC 编辑',
  convert: '谱子转换',
  query:   '问答',
  '':      '未知',
}

/**
 * FE-6: 动态获取 domain 中文标签。
 * 优先从 tool-registry.ts fetchDomainRegistry() 缓存读取（来自 /api/health/domains），
 * 缺失时降级到 DOMAIN_LABEL 硬编码兜底，最终兜底返回 domain 原始值。
 *
 * 用法（替代直接访问 DOMAIN_LABEL[domain]）：
 *   import { getDomainLabel } from '@/shared/types/trace.types'
 *   const label = getDomainLabel(trace.domain)
 */
export function getDomainLabel(domain: string): string {
  // 尝试从动态注册表获取（同步，注册表已在 fetchDomainRegistry() 中预热）
  try {
    // 动态 import 避免循环依赖（trace.types ← tool-registry），
    // 注册表已在 app 启动时 fetchDomainRegistry() 预热，此处同步读缓存
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const reg = require('@/shared/lib/tool-registry') as {
      getDomainMeta: (name: string) => { label?: string } | null
    }
    const meta = reg.getDomainMeta(domain)
    if (meta?.label) return meta.label
  } catch {
    // require 失败（SSR/测试环境），降级到静态兜底
  }
  return DOMAIN_LABEL[domain] ?? domain ?? '未知'
}
