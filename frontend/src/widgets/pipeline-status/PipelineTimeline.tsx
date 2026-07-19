'use client'
/**
 * PipelineTimeline — 审计与重播交互面板 v3.1
 *
 * 新增 & 优化：
 *   - 面板宽度可拖拽调整（480px ~ 1200px）
 *   - Trace 列表：搜索框 + domain 筛选 + 加载更多
 *   - 对话完成后自动刷新（监听 ep:audit-refresh 事件）
 *   - Span 详情：LLM 输入/输出完整内容展示（可折叠）
 *   - 审计 tab 内直接“一键提炼”当前 trace → 工作流
 *   - 底部状态栏：显示过滤结果数 / 总数
 *   - 统计摘要条、failed trace 红色高亮、token 展示、清空审计历史
 *   - ReplayPanel v2.0：HitRateRing + Token对比表 + 重放历史列表（自动刷新）+ 点击展开 span
 */

import React, { useEffect, useCallback, useRef, useState, useMemo } from 'react'
import { useTimelineStore } from '@/features/chat/store/timeline.store'
import type { SpanDto, TraceDto } from '@/shared/types/trace.types'
import { DOMAIN_LABEL } from '@/shared/types/trace.types'
import type { ReplayResponse } from '@/shared/lib/api'
import { extractWorkflow, deleteSessionTraces, listTraceReplays, getTraceDetail, exportSingleTrace } from '@/shared/lib/api'
import { downloadBlob } from '@/shared/lib/utils'
import { WorkflowPanel } from './WorkflowPanel'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const KIND_STYLE: Record<string, { bg: string; text: string; label: string }> = {
  tool:      { bg: 'bg-blue-100',   text: 'text-blue-700',   label: 'TOOL'  },
  model:     { bg: 'bg-purple-100', text: 'text-purple-700', label: 'LLM'   },
  routing:   { bg: 'bg-amber-100',  text: 'text-amber-700',  label: 'ROUTE' },
  todo_plan: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'TODO'  },
  memory:    { bg: 'bg-green-100',  text: 'text-green-700',  label: 'MEM'   },
  chain:     { bg: 'bg-pink-100',   text: 'text-pink-700',   label: 'CHAIN' },
  // v1.4 新增 span_kind（后端 trace_collector.py 产生）
  node:      { bg: 'bg-slate-100',  text: 'text-slate-600',  label: 'NODE'  },
  step:      { bg: 'bg-cyan-100',   text: 'text-cyan-700',   label: 'STEP'  },
  agent:     { bg: 'bg-indigo-100', text: 'text-indigo-700', label: 'AGENT' },
}

const STATUS_DOT: Record<string, string> = {
  ok:      'bg-emerald-400',
  running: 'bg-blue-400 animate-pulse',
  error:   'bg-red-400',
  timeout: 'bg-orange-400',
  skipped: 'bg-gray-300',
}

const TRACE_STATUS_COLOR: Record<string, string> = {
  succeeded: 'text-emerald-600',
  failed:    'text-red-500',
  running:   'text-blue-500',
  aborted:   'text-gray-400',
}

// domain 选项（用于筛选下拉）
const DOMAIN_OPTIONS = [
  { value: '',        label: '全部' },
  { value: 'sovits',  label: 'SoVITS' },
  { value: 'voice',   label: '音色' },
  { value: 'audio',   label: '音频' },
  { value: 'edit',    label: '编辑' },
  { value: 'convert', label: '转换' },
  { value: 'query',   label: '问答' },
]

// ─── 工具函数 ─────────────────────────────────────────────────────────────────

const fmtMs = (ms: number) => ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`

const fmtTime = (iso: string) => {
  if (!iso) return '--'
  try { return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return iso.slice(11, 19) }
}

const fmtRelTime = (iso: string) => {
  if (!iso) return ''
  try {
    const diff = Date.now() - new Date(iso).getTime()
    if (diff < 60000) return `${Math.round(diff / 1000)}秒前`
    if (diff < 3600000) return `${Math.round(diff / 60000)}分钟前`
    return `${Math.round(diff / 3600000)}小时前`
  } catch { return '' }
}

const tryJson = (s: string) => {
  try { return JSON.stringify(JSON.parse(s), null, 2) }
  catch { return s }
}

// ─── SpanTimeline：左侧瀑布时间线 ────────────────────────────────────────────

function SpanTimeline({ spans, selectedIdx, onSelect }: {
  spans: SpanDto[]
  selectedIdx: number | null
  onSelect: (i: number) => void
}) {
  const maxMs = Math.max(...spans.map(s => s.duration_ms), 1)

  return (
    <div className="space-y-0.5">
      {spans.map((span, i) => {
        const ks = KIND_STYLE[span.span_kind] ?? KIND_STYLE.tool
        const isErr = span.status === 'error'
        const isSelected = selectedIdx === i
        const barW = Math.max(4, Math.round((span.duration_ms / maxMs) * 100))
        // thinking span 特殊样式
        const isThinking = span.span_kind === 'model' && (
          span.tool_name === 'thinking' ||
          (span.tool_result_preview ?? '').startsWith('<think>')
        )

        return (
          <div
            key={span.span_id}
            onClick={() => onSelect(i)}
            className={`group flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-all select-none
              ${isSelected
                ? (isThinking ? 'bg-violet-50 ring-1 ring-violet-300' : 'bg-blue-50 ring-1 ring-blue-300')
                : (isThinking ? 'hover:bg-violet-50/60' : 'hover:bg-gray-50')
              }`}
          >
            <span className="text-[10px] text-gray-400 w-4 text-right shrink-0 font-mono">{i + 1}</span>
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[span.status] ?? 'bg-gray-300'}`} />
            {isThinking ? (
              <span className="text-[9px] px-1 py-0.5 rounded font-bold shrink-0 bg-violet-100 text-violet-700">
                THINK
              </span>
            ) : (
              <span className={`text-[9px] px-1 py-0.5 rounded font-bold shrink-0 ${ks.bg} ${ks.text}`}>
                {ks.label}
              </span>
            )}
            <span className={`text-xs truncate flex-1 ${
              isErr ? 'text-red-600'
              : isThinking ? (isSelected ? 'text-violet-700 font-medium' : 'text-violet-600')
              : isSelected ? 'text-blue-700 font-medium'
              : 'text-gray-700'
            }`}>
              {isThinking ? '🧠 思考过程' : (span.tool_name || span.agent_name || `round_${span.round_idx}`)}
            </span>
            <div className="w-16 shrink-0 flex items-center gap-1">
              <div className="flex-1 h-1 bg-gray-100 rounded-full overflow-hidden">
                <div className={`h-full rounded-full ${isErr ? 'bg-red-300' : isThinking ? 'bg-violet-300' : 'bg-blue-300'}`}
                  style={{ width: `${barW}%` }} />
              </div>
              <span className="text-[9px] text-gray-400 w-8 text-right font-mono shrink-0">{fmtMs(span.duration_ms)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ─── SpanDetail：右侧 Span 详情卡（v3：思考链专属渲染）─────────────────────────

function SpanDetail({ span }: { span: SpanDto }) {
  const ks = KIND_STYLE[span.span_kind] ?? KIND_STYLE.tool
  const isErr = span.status === 'error'
  const isModel = span.span_kind === 'model'
  // thinking span：tool_name='thinking' 或 tool_result_preview 以 '<think>' 开头
  const isThinking = isModel && (
    span.tool_name === 'thinking' ||
    (span.tool_result_preview ?? '').startsWith('<think>')
  )
  const [showFullResult, setShowFullResult] = useState(false)
  const [showFullArgs, setShowFullArgs] = useState(false)
  // 思考链默认展开（内容较重要）
  const [thinkExpanded, setThinkExpanded] = useState(true)

  // 解析 tool_result（可能含 LLM 完整输出 / 思考链）
  // 思考链是纯文本，不做 JSON 解析
  let fullResult = ''
  let fullArgs = ''
  try {
    fullResult = isThinking
      ? (span.tool_result ?? '')
      : (span.tool_result ? tryJson(span.tool_result) : '')
  } catch { fullResult = span.tool_result ?? '' }
  try { fullArgs = span.tool_args ? tryJson(span.tool_args) : '' } catch { fullArgs = span.tool_args ?? '' }

  // 头部标题：thinking span 显示特殊标签
  const titleText = isThinking
    ? '🧠 思考过程'
    : (span.tool_name || span.agent_name || `round_${span.round_idx}`)

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* 头部 */}
      <div className={`px-4 py-3 border-b shrink-0 ${isThinking ? 'border-violet-100 bg-violet-50/40' : 'border-gray-100'}`}>
        <div className="flex items-center gap-2 mb-1">
          {isThinking ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-bold bg-violet-100 text-violet-700">THINK</span>
          ) : (
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${ks.bg} ${ks.text}`}>{ks.label}</span>
          )}
          <span className={`w-2 h-2 rounded-full ${STATUS_DOT[span.status] ?? 'bg-gray-300'}`} />
          <span className={`text-xs font-medium ${isErr ? 'text-red-600' : 'text-gray-500'}`}>{span.status}</span>
          <span className="ml-auto text-xs font-mono text-gray-500">{fmtMs(span.duration_ms)}</span>
        </div>
        <div className={`text-sm font-semibold truncate ${isThinking ? 'text-violet-800' : 'text-gray-800'}`}>
          {titleText}
        </div>
        <div className="text-[10px] text-gray-400 mt-0.5 font-mono">
          {fmtTime(span.started_at)} → {fmtTime(span.ended_at)}
          {span.call_id && <span className="ml-2 text-gray-300">#{span.call_id.slice(-6)}</span>}
        </div>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs">

        {/* 模型信息（LLM span 专属） */}
        {isModel && span.model && (
          <section>
            <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1.5">模型 Model</div>
            <div className="flex flex-wrap gap-2 text-gray-600 items-center">
              <span className="bg-purple-50 px-2 py-0.5 rounded text-purple-700 font-medium">{span.model}</span>
              {span.input_tokens > 0 && (
                <span className="bg-gray-50 px-2 py-0.5 rounded text-gray-500">
                  输入 <b className="text-gray-700">{span.input_tokens.toLocaleString()}</b> tokens
                </span>
              )}
              {span.output_tokens > 0 && (
                <span className="bg-gray-50 px-2 py-0.5 rounded text-gray-500">
                  输出 <b className="text-gray-700">{span.output_tokens.toLocaleString()}</b> tokens
                </span>
              )}
              {span.finish_reason && (
                <span className={`px-2 py-0.5 rounded text-[10px] font-medium
                  ${span.finish_reason === 'stop' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'}`}>
                  {span.finish_reason}
                </span>
              )}
            </div>
          </section>
        )}

        {/* ── 思考链专属渲染区 ── */}
        {isThinking && fullResult && (
          <section>
            <div className="flex items-center justify-between mb-1.5">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-bold text-violet-500 uppercase tracking-wider">思考链 Reasoning</span>
                <span className="text-[9px] text-violet-300 bg-violet-50 px-1.5 py-0.5 rounded-full">
                  {fullResult.length.toLocaleString()} 字符
                </span>
              </div>
              <button
                onClick={() => setThinkExpanded(v => !v)}
                className="text-[10px] text-violet-400 hover:text-violet-600 transition-colors flex items-center gap-1"
              >
                <span className={`inline-block transition-transform ${thinkExpanded ? 'rotate-90' : ''}`}>▶</span>
                {thinkExpanded ? '收起' : '展开'}
              </button>
            </div>
            {thinkExpanded && (
              <div className="relative">
                {/* 左侧紫色竖线装饰 */}
                <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-violet-200 rounded-full" />
                <pre className="pl-3 bg-violet-50/60 border border-violet-100 rounded-lg p-3 text-[11px]
                  text-violet-900 whitespace-pre-wrap break-words leading-relaxed font-mono
                  max-h-[480px] overflow-y-auto">
                  {fullResult}
                </pre>
              </div>
            )}
          </section>
        )}

        {/* 参数 Args（工具调用 / LLM prompt 均显示，思考 span 跳过） */}
        {!isThinking && fullArgs && fullArgs !== '{}' && (
          <section>
            <div className="flex items-center justify-between mb-1">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                {isModel ? '提示词 Prompt' : '参数 Args'}
              </div>
              {fullArgs.length > 300 && (
                <button
                  onClick={() => setShowFullArgs(v => !v)}
                  className="text-[10px] text-blue-400 hover:text-blue-600 transition-colors"
                >
                  {showFullArgs ? '收起' : `展开全部 (${fullArgs.length}字符)`}
                </button>
              )}
            </div>
            <pre className={`bg-gray-50 border border-gray-100 rounded-lg p-2.5 text-[11px] text-gray-700
              overflow-x-auto whitespace-pre-wrap break-all leading-relaxed font-mono
              ${!showFullArgs && fullArgs.length > 300 ? 'max-h-32 overflow-y-hidden' : 'max-h-64 overflow-y-auto'}`}>
              {fullArgs}
            </pre>
          </section>
        )}

        {/* 返回值 / LLM 完整输出（思考 span 已在上方专属区域展示，此处跳过） */}
        {!isThinking && (span.tool_result_preview || fullResult) && (
          <section>
            <div className="flex items-center justify-between mb-1">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                {isModel ? '完整输出 Output' : '返回值 Result'}
              </div>
              {fullResult && fullResult.length > 200 && (
                <button
                  onClick={() => setShowFullResult(v => !v)}
                  className="text-[10px] text-blue-400 hover:text-blue-600 transition-colors"
                >
                  {showFullResult ? '收起' : `展开全部 (${fullResult.length}字符)`}
                </button>
              )}
            </div>
            {showFullResult && fullResult ? (
              <pre className="bg-emerald-50 border border-emerald-100 rounded-lg p-2.5 text-emerald-800
                break-all leading-relaxed max-h-64 overflow-y-auto whitespace-pre-wrap font-mono text-[11px]">
                {fullResult}
              </pre>
            ) : (
              <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-2.5 text-emerald-800 break-all leading-relaxed">
                {span.tool_result_preview || fullResult.slice(0, 200)}
                {!showFullResult && fullResult.length > 200 && (
                  <span className="text-emerald-400 text-[10px]"> …</span>
                )}
              </div>
            )}
          </section>
        )}

        {/* 错误 */}
        {isErr && span.error_msg && (
          <section>
            <div className="text-[10px] font-bold text-red-400 uppercase tracking-wider mb-1">错误 Error</div>
            <div className="bg-red-50 border border-red-100 rounded-lg p-2.5 text-red-700 break-all leading-relaxed">
              {span.error_msg}
            </div>
          </section>
        )}

        {/* hash（调试用） */}
        {span.tool_args_hash && span.tool_args_hash !== 'unknown' && (
          <div className="text-[10px] text-gray-300 font-mono">hash: {span.tool_args_hash}</div>
        )}
      </div>
    </div>
  )
}

// ─── ReplayPanel：重播操作区 ──────────────────────────────────────────────────

// ─── ReplayPanel v2.0：命中率环形图 + 重放历史 + 差异增强 ─────────────────────

/** 命中率环形进度圈 */
function HitRateRing({ rate, size = 48 }: { rate: number; size?: number }) {
  const r = (size - 6) / 2
  const circ = 2 * Math.PI * r
  const dash = circ * rate
  const color = rate >= 0.8 ? '#10b981' : rate >= 0.5 ? '#f59e0b' : '#ef4444'
  return (
    <svg width={size} height={size} className="shrink-0">
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="#f3f4f6" strokeWidth={5} />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={5}
        strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
        transform={`rotate(-90 ${size/2} ${size/2})`} />
      <text x={size/2} y={size/2+4} textAnchor="middle" fontSize={10} fontWeight="bold" fill={color}>
        {Math.round(rate * 100)}%
      </text>
    </svg>
  )
}

function ReplayPanel({ traceId }: { traceId: string }) {
  const {
    replayingTraceId, replayResult, replaySteps, replayCurrentStep,
    startReplay, clearReplay, error: storeError,
  } = useTimelineStore()
  const isReplaying = replayingTraceId === traceId
  const result = replayResult

  // 重放历史
  const [history, setHistory] = useState<Array<{
    replay_id: string; mode: string; status: string
    diff_summary: string; created_at: string; replay_trace_id: string
  }>>([])
  const [showHistory, setShowHistory] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)

  // 展开的历史条目：key = replay_trace_id，value = spans 数组
  const [expandedSpans, setExpandedSpans] = useState<Record<string, SpanDto[]>>({})
  const [loadingSpans, setLoadingSpans] = useState<Record<string, boolean>>({})

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true)
    try {
      const res = await listTraceReplays(traceId, 8)
      setHistory(res.replays ?? [])
    } catch { /* 静默 */ }
    finally { setLoadingHistory(false) }
  }, [traceId])

  // 首次展开历史时加载
  useEffect(() => {
    if (showHistory) void loadHistory()
  }, [showHistory, loadHistory])

  // 重放完成后自动刷新历史
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ traceId: string }>).detail
      if (detail?.traceId === traceId && showHistory) {
        void loadHistory()
      } else if (detail?.traceId === traceId) {
        // 重放完成后自动展开历史
        setShowHistory(true)
      }
    }
    window.addEventListener('ep:replay-done', handler)
    return () => window.removeEventListener('ep:replay-done', handler)
  }, [traceId, showHistory, loadHistory])

  // 点击展开/收起某条历史的 span 详情
  const toggleSpans = useCallback(async (replayTraceId: string) => {
    if (expandedSpans[replayTraceId]) {
      // 已展开则收起
      setExpandedSpans(prev => { const n = { ...prev }; delete n[replayTraceId]; return n })
      return
    }
    setLoadingSpans(prev => ({ ...prev, [replayTraceId]: true }))
    try {
      const res = await getTraceDetail(replayTraceId)
      setExpandedSpans(prev => ({ ...prev, [replayTraceId]: res.spans ?? [] }))
    } catch { /* 静默 */ }
    finally { setLoadingSpans(prev => ({ ...prev, [replayTraceId]: false })) }
  }, [expandedSpans])

  const hitRate = result?.fixture_hit_rate ?? 1
  const totalFixtures = result?.total_fixtures ?? 0
  const tokenDiff = result?.token_diff

  // 重放完成后自动滚动结果区到可见位置
  const resultRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [result])

  return (
    <div className="border-t border-gray-100 px-4 py-3 space-y-2.5 shrink-0">
      {/* 标题行 */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">一键重播</span>
        <button
          onClick={() => setShowHistory(v => !v)}
          className="text-[10px] text-blue-400 hover:text-blue-600 transition-colors"
        >
          {showHistory ? '隐藏历史' : '查看历史'}
        </button>
      </div>

      {/* 操作按钮 */}
      <div className="flex gap-2">
        <button
          onClick={() => { clearReplay(); startReplay(traceId, 'fixture') }}
          disabled={isReplaying}
          className={`flex-1 flex items-center justify-center gap-1.5 text-xs py-2 rounded-lg border font-medium transition-all
            ${isReplaying
              ? 'bg-blue-50 text-blue-300 border-blue-100 cursor-wait'
              : 'bg-blue-500 text-white border-blue-500 hover:bg-blue-600 shadow-sm'
            }`}
          title="用冻结返回值重播（快速/确定性）"
        >
          <span className={isReplaying ? 'animate-spin inline-block' : ''}>⟳</span>
          {isReplaying ? '重播中…' : 'Fixture 重播'}
        </button>
        <button
          onClick={() => { clearReplay(); startReplay(traceId, 'live') }}
          disabled={isReplaying}
          className={`flex-1 flex items-center justify-center gap-1.5 text-xs py-2 rounded-lg border font-medium transition-all
            ${isReplaying
              ? 'bg-gray-50 text-gray-300 border-gray-100 cursor-wait'
              : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300'
            }`}
          title="真实调用所有工具（验证修复后行为）"
        >
          {isReplaying ? <span className="animate-spin inline-block">⟳</span> : '▶'} Live 重播
        </button>
      </div>

      {/* ── 实时进度面板（重播中） ── */}
      {isReplaying && (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 space-y-2">
          {/* 当前步骤描述 */}
          <div className="flex items-center gap-2">
            <span className="animate-spin text-blue-400 text-xs">⟳</span>
            <span className="text-[11px] text-blue-700 font-medium truncate flex-1">
              {replayCurrentStep ?? '准备中…'}
            </span>
            <span className="text-[10px] text-blue-400 shrink-0">{replaySteps.length} 步</span>
          </div>
          {/* 步骤列表（最近 5 步） */}
          {replaySteps.length > 0 && (
            <div className="space-y-0.5 max-h-24 overflow-y-auto">
              {[...replaySteps].reverse().slice(0, 5).map((s, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[10px]">
                  <span className={`w-1 h-1 rounded-full shrink-0 ${
                    s.status === 'error' ? 'bg-red-400' :
                    s.status === 'ok'    ? 'bg-emerald-400' : 'bg-blue-300 animate-pulse'
                  }`} />
                  <span className="font-mono text-blue-600 shrink-0 truncate max-w-[80px]">
                    {s.tool ?? String(s.step)}
                  </span>
                  <span className="text-blue-400 truncate flex-1">{s.text ?? ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 错误详情面板（重播失败） ── */}
      {!isReplaying && storeError && !result && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-red-600">✗ 重播失败</span>
            <button
              onClick={clearReplay}
              className="text-red-300 hover:text-red-500 w-5 h-5 flex items-center justify-center rounded"
            >×</button>
          </div>
          <div className="text-[11px] text-red-700 break-all leading-relaxed bg-red-100 rounded p-2 font-mono">
            {storeError}
          </div>
        </div>
      )}

      {/* 重放结果 */}
      {result && (
        <div ref={resultRef} className={`rounded-lg p-3 text-xs space-y-2.5
          ${result.status === 'succeeded' ? 'bg-emerald-50 border border-emerald-200' : 'bg-red-50 border border-red-200'}`}>

          {/* 结果头 */}
          <div className="flex items-center justify-between">
            <span className={`font-semibold ${result.status === 'succeeded' ? 'text-emerald-700' : 'text-red-600'}`}>
              {result.status === 'succeeded' ? '✓ 重播完成' : '✗ 重播失败'}
              {result.mode && <span className="ml-1 text-[10px] font-normal opacity-60">({result.mode})</span>}
            </span>
            <button onClick={clearReplay} className="text-gray-400 hover:text-gray-600 w-5 h-5 flex items-center justify-center rounded hover:bg-gray-100">×</button>
          </div>

          {/* 差异摘要 */}
          <div className={`font-medium ${result.diff_summary.includes('完全一致') ? 'text-emerald-700' : 'text-amber-700'}`}>
            {result.diff_summary.includes('完全一致') ? '✓' : '△'} {result.diff_summary}
          </div>

          {/* Fixture 命中率（仅 fixture 模式） */}
          {result.mode === 'fixture' && totalFixtures > 0 && (
            <div className="flex items-center gap-3 bg-white/60 rounded-lg p-2">
              <HitRateRing rate={hitRate} size={44} />
              <div className="flex-1 space-y-0.5">
                <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Fixture 命中率</div>
                <div className="text-gray-700">
                  <span className="font-medium">{result.fixture_hits?.length ?? 0}</span>
                  <span className="text-gray-400"> / {totalFixtures} 个工具命中</span>
                </div>
                {result.fixture_misses.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {result.fixture_misses.map((m, i) => (
                      <span key={i} className="bg-amber-100 text-amber-800 px-1 py-0.5 rounded text-[9px] font-mono">
                        ✗ {m.tool_name}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Token 差异（有数据才展示） */}
          {tokenDiff && (tokenDiff.src_input > 0 || tokenDiff.rep_input > 0) && (
            <div className="bg-white/60 rounded-lg p-2 space-y-1">
              <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Token 对比</div>
              <div className="grid grid-cols-3 gap-1 text-[10px]">
                <div className="text-gray-400">指标</div>
                <div className="text-gray-500 text-center">原始</div>
                <div className="text-gray-500 text-center">重播</div>
                <div className="text-gray-600">输入</div>
                <div className="text-center font-mono">{tokenDiff.src_input.toLocaleString()}</div>
                <div className={`text-center font-mono ${tokenDiff.delta_input > 0 ? 'text-red-500' : tokenDiff.delta_input < 0 ? 'text-emerald-600' : 'text-gray-600'}`}>
                  {tokenDiff.rep_input.toLocaleString()}
                  {tokenDiff.delta_input !== 0 && <span className="ml-0.5 text-[9px]">({tokenDiff.delta_input > 0 ? '+' : ''}{tokenDiff.delta_input})</span>}
                </div>
                <div className="text-gray-600">输出</div>
                <div className="text-center font-mono">{tokenDiff.src_output.toLocaleString()}</div>
                <div className={`text-center font-mono ${tokenDiff.delta_output > 0 ? 'text-red-500' : tokenDiff.delta_output < 0 ? 'text-emerald-600' : 'text-gray-600'}`}>
                  {tokenDiff.rep_output.toLocaleString()}
                  {tokenDiff.delta_output !== 0 && <span className="ml-0.5 text-[9px]">({tokenDiff.delta_output > 0 ? '+' : ''}{tokenDiff.delta_output})</span>}
                </div>
              </div>
            </div>
          )}

          {/* 差异步骤详情 */}
          {result.diff_details.filter(d => !d.match).length > 0 && (
            <details className="group">
              <summary className="cursor-pointer text-gray-500 hover:text-gray-700 font-medium flex items-center gap-1">
                <span className="text-[10px] group-open:rotate-90 transition-transform inline-block">▶</span>
                查看 {result.diff_details.filter(d => !d.match).length} 处步骤差异
              </summary>
              <div className="mt-1.5 space-y-1 max-h-32 overflow-y-auto">
                {result.diff_details.filter(d => !d.match).map((d, i) => (
                  <div key={i} className="grid grid-cols-2 gap-1 bg-white rounded p-1.5 border border-amber-100">
                    <div className="text-gray-500">
                      <span className="text-[9px] text-gray-400 block">原始 步骤{d.step + 1}</span>
                      <span className="font-mono text-[10px]">{String((d.source as {tool?: string} | null)?.tool ?? '—')}</span>
                      {(d.source as {hash?: string} | null)?.hash && (
                        <span className="text-[8px] text-gray-300 block font-mono truncate">
                          #{(d.source as {hash: string}).hash.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    <div className="text-blue-600">
                      <span className="text-[9px] text-blue-400 block">重播</span>
                      <span className="font-mono text-[10px]">{String((d.replay as {tool?: string} | null)?.tool ?? '—')}</span>
                      {(d.replay as {hash?: string} | null)?.hash && (
                        <span className="text-[8px] text-blue-300 block font-mono truncate">
                          #{(d.replay as {hash: string}).hash.slice(0, 8)}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* 错误详情（result.status === failed 时） */}
          {result.status !== 'succeeded' && result.error_detail && (
            <div className="bg-red-50 border border-red-100 rounded-lg p-2 space-y-1">
              <div className="text-[10px] font-bold text-red-400 uppercase tracking-wider">错误详情</div>
              <div className="text-[11px] text-red-700 break-all leading-relaxed font-mono">
                {result.error_detail}
              </div>
            </div>
          )}

          {result.trace_id && (
            <div className="text-[10px] text-gray-400 font-mono truncate">重播 trace: {result.trace_id.slice(-12)}</div>
          )}
        </div>
      )}

      {/* 重放历史列表 */}
      {showHistory && (
        <div className="border border-gray-100 rounded-lg overflow-hidden">
          <div className="px-3 py-1.5 bg-gray-50 flex items-center justify-between">
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">重放历史</span>
            <button onClick={() => void loadHistory()} disabled={loadingHistory}
              className="text-[10px] text-gray-400 hover:text-gray-600 disabled:opacity-40">
              {loadingHistory ? <span className="animate-spin inline-block">⟳</span> : '↻'}
            </button>
          </div>
          {history.length === 0 && !loadingHistory ? (
            <div className="py-4 text-center text-[11px] text-gray-400">暂无重放记录</div>
          ) : loadingHistory && history.length === 0 ? (
            <div className="py-4 text-center text-[11px] text-gray-400">
              <span className="animate-spin inline-block mr-1">⟳</span>加载中…
            </div>
          ) : (
            <div className="divide-y divide-gray-50 max-h-64 overflow-y-auto">
              {history.map((h) => (
                <div key={h.replay_id}>
                  {/* 历史条目头部 */}
                  <div
                    className="px-3 py-2 flex items-center gap-2 cursor-pointer hover:bg-gray-50 transition-colors"
                    onClick={() => void toggleSpans(h.replay_trace_id)}
                    title="点击展开/收起重放链路详情"
                  >
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${h.status === 'succeeded' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                    <span className={`text-[9px] px-1 rounded font-bold shrink-0
                      ${h.mode === 'fixture' ? 'bg-blue-100 text-blue-600' : 'bg-orange-100 text-orange-600'}`}>
                      {h.mode === 'fixture' ? 'FIX' : 'LIVE'}
                    </span>
                    <span className="text-[10px] text-gray-600 flex-1 truncate">{h.diff_summary}</span>
                    <span className="text-[9px] text-gray-400 shrink-0 font-mono">
                      {h.created_at ? new Date(h.created_at).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'}) : ''}
                    </span>
                    {loadingSpans[h.replay_trace_id] ? (
                      <span className="text-[9px] text-blue-400 animate-spin">⟳</span>
                    ) : (
                      <span className={`text-[9px] text-gray-300 transition-transform ${expandedSpans[h.replay_trace_id] ? 'rotate-90' : ''}`}>▶</span>
                    )}
                  </div>

                  {/* 内联展开的 span 列表 */}
                  {expandedSpans[h.replay_trace_id] && (
                    <div className="bg-gray-50 border-t border-gray-100 px-3 py-2 space-y-0.5">
                      {expandedSpans[h.replay_trace_id].length === 0 ? (
                        <div className="text-[10px] text-gray-400 text-center py-1">暂无工具调用记录</div>
                      ) : (
                        expandedSpans[h.replay_trace_id].map((span, si) => {
                          const ks = KIND_STYLE[span.span_kind] ?? KIND_STYLE.tool
                          const isErr = span.status === 'error'
                          return (
                            <div key={span.span_id} className="flex items-center gap-1.5 text-[10px]">
                              <span className="text-gray-300 w-3 text-right font-mono shrink-0">{si + 1}</span>
                              <span className={`w-1 h-1 rounded-full shrink-0 ${STATUS_DOT[span.status] ?? 'bg-gray-300'}`} />
                              <span className={`text-[8px] px-1 py-0.5 rounded font-bold shrink-0 ${ks.bg} ${ks.text}`}>{ks.label}</span>
                              <span className={`truncate flex-1 ${isErr ? 'text-red-500' : 'text-gray-600'}`}>
                                {span.tool_name || span.agent_name || `round_${span.round_idx}`}
                              </span>
                              <span className="text-gray-300 font-mono shrink-0">{fmtMs(span.duration_ms)}</span>
                            </div>
                          )
                        })
                      )}
                      <div className="text-[9px] text-gray-300 font-mono pt-0.5 truncate">
                        trace: {h.replay_trace_id.slice(-12)}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── ExtractButton：一键提炼（审计 tab 内嵌）─────────────────────────────────

function ExtractButton({ traceId, onDone }: { traceId: string; onDone?: (name: string) => void }) {
  const [extracting, setExtracting] = useState(false)
  const [msg, setMsg] = useState('')

  const handleExtract = useCallback(async () => {
    setExtracting(true)
    setMsg('')
    try {
      const res = await extractWorkflow(traceId, true)
      setMsg(`✓ 已提炼「${res.name ?? res.template_id}」`)
      onDone?.(res.name ?? res.template_id ?? '')
    } catch (e: unknown) {
      setMsg(`✗ ${e instanceof Error ? e.message : '提炼失败'}`)
    } finally {
      setExtracting(false)
    }
  }, [traceId, onDone])

  return (
    <div className="px-4 pt-2 pb-1 border-t border-gray-50 shrink-0 space-y-1">
      <button
        onClick={handleExtract}
        disabled={extracting}
        className="w-full flex items-center justify-center gap-1.5 text-[11px] py-1.5 rounded-md border border-dashed
          border-blue-300 hover:border-blue-500 hover:bg-blue-50 text-blue-500 transition-all disabled:opacity-60"
        title="用 LLM 分析此 Trace，提炼为可重复执行的工作流模板"
      >
        {extracting
          ? <><span className="w-3 h-3 border border-blue-400 border-t-transparent rounded-full animate-spin" />提炼中…</>
          : <>✦ 提炼为工作流</>
        }
      </button>
      {msg && (
        <p className={`text-[10px] text-center ${msg.startsWith('✓') ? 'text-emerald-600' : 'text-red-500'}`}>{msg}</p>
      )}
    </div>
  )
}

// ─── TraceListItem：左侧 Trace 列表项 ────────────────────────────────────────

function TraceListItem({ trace, isActive, onClick }: {
  trace: TraceDto; isActive: boolean; onClick: () => void
}) {
  const domainLabel = DOMAIN_LABEL[trace.domain] ?? (trace.domain || '未知')
  const statusCls = TRACE_STATUS_COLOR[trace.status] ?? 'text-gray-400'

  const isFailed = trace.status === 'failed'
  return (
    <div
      onClick={onClick}
      className={`px-3 py-2.5 cursor-pointer border-b border-gray-50 transition-all select-none
        ${isActive
          ? 'bg-blue-50 border-l-2 border-l-blue-400'
          : isFailed
            ? 'hover:bg-red-50 border-l-2 border-l-red-300 bg-red-50/40'
            : 'hover:bg-gray-50 border-l-2 border-l-transparent'
        }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[9px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded font-bold shrink-0">
          {domainLabel.length > 8 ? domainLabel.slice(0, 8) : domainLabel}
        </span>
        <span className={`text-[10px] font-medium ${statusCls}`}>
          {trace.status === 'succeeded' ? '✓' : trace.status === 'failed' ? '✗' : '⟳'}
        </span>
        <span className="ml-auto text-[10px] text-gray-400 shrink-0 font-mono">
          {trace.total_steps}步 {fmtMs(trace.duration_ms)}
        </span>
      </div>
      <div className={`text-xs truncate ${isActive ? 'text-blue-700 font-medium' : 'text-gray-600'}`}>
        {trace.user_message || '（无消息）'}
      </div>
      <div className="flex items-center justify-between mt-0.5">
        <span className="text-[10px] text-gray-400">{fmtRelTime(trace.started_at)}</span>
        {(trace.input_tokens > 0 || trace.output_tokens > 0) && (
          <span className="text-[9px] text-purple-400 font-mono">
            {(trace.input_tokens + trace.output_tokens).toLocaleString()}tok
          </span>
        )}
      </div>
    </div>
  )
}

// ─── PipelineTimeline：主面板 ─────────────────────────────────────────────────

interface PipelineTimelineProps {
  sessionId: string
}

// ─── 层级选择器组件 ───────────────────────────────────────────────────────────
function HierarchyFilter({
  onFilterChange,
  currentWsId, currentProjId, currentSessId,
}: {
  onFilterChange: (wsId: string, projId: string, sessId: string) => void
  currentWsId: string
  currentProjId: string
  currentSessId: string
}) {
  const { workspaces } = useWorkspaceStore()

  // 当前选中工作区的项目列表
  const projects = useMemo(() => {
    if (!currentWsId) return []
    return workspaces.find(w => w.id === currentWsId)?.projects ?? []
  }, [workspaces, currentWsId])

  // 当前选中项目的 session 列表
  const sessions = useMemo(() => {
    if (!currentProjId) {
      // 未选项目时，显示工作区下所有 session
      if (!currentWsId) return []
      const ws = workspaces.find(w => w.id === currentWsId)
      return ws?.sessions ?? []
    }
    return projects.find(p => p.id === currentProjId)?.sessions ?? []
  }, [workspaces, projects, currentWsId, currentProjId])

  return (
    <div className="px-2 py-2 border-b border-gray-100 bg-blue-50/40 shrink-0 space-y-1">
      <div className="text-[9px] font-bold text-gray-400 uppercase tracking-wider px-1 mb-1">层级筛选</div>
      {/* 工作区 */}
      <select
        value={currentWsId}
        onChange={e => onFilterChange(e.target.value, '', '')}
        className="w-full text-[11px] border border-gray-200 rounded-md px-2 py-1
                   focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white text-gray-600"
      >
        <option value="">🏢 全部工作区</option>
        {workspaces.map(ws => (
          <option key={ws.id} value={ws.id}>{ws.name}</option>
        ))}
      </select>
      {/* 项目（选了工作区才显示） */}
      {currentWsId && (
        <select
          value={currentProjId}
          onChange={e => onFilterChange(currentWsId, e.target.value, '')}
          className="w-full text-[11px] border border-gray-200 rounded-md px-2 py-1
                     focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white text-gray-600"
        >
          <option value="">📁 全部项目</option>
          {projects.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      )}
      {/* 话题/Session（选了工作区才显示） */}
      {currentWsId && (
        <select
          value={currentSessId}
          onChange={e => onFilterChange(currentWsId, currentProjId, e.target.value)}
          className="w-full text-[11px] border border-gray-200 rounded-md px-2 py-1
                     focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white text-gray-600"
        >
          <option value="">💬 全部话题</option>
          {sessions.map(s => (
            <option key={s.id} value={s.id}>{s.title || '新对话'}</option>
          ))}
        </select>
      )}
      {/* 清除层级筛选 */}
      {(currentWsId || currentProjId || currentSessId) && (
        <button
          onClick={() => onFilterChange('', '', '')}
          className="w-full text-[10px] text-blue-400 hover:text-blue-600 py-0.5"
        >
          ✕ 清除层级筛选
        </button>
      )}
    </div>
  )
}

export function PipelineTimeline({ sessionId }: PipelineTimelineProps) {
  const {
    isOpen, traces, spans, loading, loadingMore, error, hasMore,
    selectedTraceId, panelWidth, stats,
    filterDomain, filterKeyword,
    filterWorkspaceId, filterProjectId, filterSessionId,
    loadTraces, loadMore, loadStats, selectTrace, clearTrace, closePanel,
    setFilterDomain, setFilterKeyword, setPanelWidth,
    setHierarchyFilter,
    filteredTraces,
  } = useTimelineStore()

  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [clearing, setClearing] = useState(false)

  const [selectedSpanIdx, setSelectedSpanIdx] = React.useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<'audit' | 'workflow'>('audit')
  const [extractDoneMsg, setExtractDoneMsg] = useState('')
  const prevTraceId = useRef<string | null>(null)

  // ── 面板打开时自动加载（含统计） ────────────────────────────────────────────
  // 优先使用 store 中的层级筛选，无筛选时用当前 sessionId
  useEffect(() => {
    if (isOpen) {
      void loadTraces(sessionId)
      void loadStats(sessionId)
    }
  }, [isOpen, sessionId, loadTraces, loadStats])

  // ── 对话完成后自动刷新（增量 merge，不闪烁） ─────────────────────────────
  // ChatPanel 在 message.completed 时 dispatch ep:audit-refresh 事件
  useEffect(() => {
    if (!isOpen) return
    const handler = () => {
      void loadTraces(sessionId, true)   // merge=true：增量追加新 trace，不替换旧列表
      void loadStats(sessionId)
    }
    window.addEventListener('ep:audit-refresh', handler)
    return () => window.removeEventListener('ep:audit-refresh', handler)
  }, [isOpen, sessionId, loadTraces, loadStats])

  // ── 切换 trace 时重置 span 选中 ───────────────────────────────────────────
  useEffect(() => {
    if (selectedTraceId !== prevTraceId.current) {
      setSelectedSpanIdx(null)
      prevTraceId.current = selectedTraceId
    }
  }, [selectedTraceId])

  // ── 自动选中第一个 span ────────────────────────────────────────────────────
  useEffect(() => {
    if (spans.length > 0 && selectedSpanIdx === null) setSelectedSpanIdx(0)
  }, [spans])

  // ── 面板宽度拖拽 ──────────────────────────────────────────────────────────
  const draggingRef = useRef(false)
  const dragStartX = useRef(0)
  const dragStartW = useRef(panelWidth)

  const onDividerMouseDown = useCallback((e: React.MouseEvent) => {
    draggingRef.current = true
    dragStartX.current = e.clientX
    dragStartW.current = panelWidth
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return
      const dx = dragStartX.current - ev.clientX  // 向左拖 = 变宽
      setPanelWidth(dragStartW.current + dx)
    }
    const onUp = () => {
      draggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [panelWidth, setPanelWidth])

  const handleSelectTrace = useCallback((traceId: string) => {
    if (selectedTraceId === traceId) clearTrace()
    else selectTrace(traceId)
  }, [selectedTraceId, selectTrace, clearTrace])

  if (!isOpen) return null

  const displayed = filteredTraces()
  const selectedSpan = selectedSpanIdx !== null ? spans[selectedSpanIdx] : null
  const activeTrace = traces.find(t => t.trace_id === selectedTraceId) ?? null

  return (
    <div
      className="fixed right-0 top-0 h-full bg-white shadow-2xl border-l border-gray-200 z-50 flex"
      style={{ width: panelWidth }}
    >
      {/* 左侧拖拽条 */}
      <div
        onMouseDown={onDividerMouseDown}
        className="w-1 shrink-0 cursor-col-resize hover:bg-blue-300 active:bg-blue-400 transition-colors bg-gray-100 group flex flex-col items-center justify-center gap-1"
        title="拖拽调整面板宽度"
      >
        {[0,1,2].map(i => <span key={i} className="w-0.5 h-0.5 rounded-full bg-gray-300 group-hover:bg-blue-400" />)}
      </div>

      {/* 主体 */}
      <div className="flex-1 flex flex-col overflow-hidden">

        {/* ── 顶部栏 ── */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-200 bg-gray-50 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-sm">🔍</span>
            <span className="font-semibold text-gray-800 text-sm">审计链路 & 重播</span>
            {traces.length > 0 && (
              <span className="text-[10px] bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full font-medium">
                {traces.length} 条
              </span>
            )}
            {stats && stats.failed > 0 && (
              <span className="text-[10px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full font-medium animate-pulse">
                {stats.failed} 失败
              </span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => { void loadTraces(sessionId); void loadStats(sessionId) }}
              disabled={loading}
              title={filterWorkspaceId ? `当前筛选: ${filterWorkspaceId.slice(0,6)}…` : '刷新全部'}
              className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1 rounded hover:bg-gray-200 transition-colors disabled:opacity-40"
            >
              <span className={loading ? 'animate-spin inline-block' : ''}>↻</span> 刷新
            </button>
            {/* 清空审计历史 */}
            <button
              onClick={() => setShowClearConfirm(true)}
              className="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded hover:bg-red-50 transition-colors"
              title="清空本 session 所有审计记录"
            >🗑</button>
            <button
              onClick={closePanel}
              className="text-gray-400 hover:text-gray-600 w-7 h-7 flex items-center justify-center rounded hover:bg-gray-200 transition-colors text-lg leading-none"
            >×</button>
          </div>
        </div>

        {/* ── 统计摘要条 ── */}
        {stats && stats.total > 0 && (
          <div className="flex items-center gap-3 px-4 py-1.5 border-b border-gray-100 bg-gray-50 shrink-0 text-[10px] text-gray-500 overflow-x-auto">
            <span className="shrink-0">
              <span className="text-emerald-600 font-medium">{stats.succeeded}</span> 成功
              {stats.failed > 0 && <span className="text-red-500 font-medium ml-1.5">{stats.failed}</span>}
              {stats.failed > 0 && <span className="ml-0.5">失败</span>}
            </span>
            <span className="text-gray-300">|</span>
            {(stats.total_input_tokens > 0 || stats.total_output_tokens > 0) && (
              <span className="shrink-0">
                🔤 <span className="font-medium text-purple-600">{(stats.total_input_tokens + stats.total_output_tokens).toLocaleString()}</span> tokens
                <span className="text-gray-400 ml-1">({stats.total_input_tokens.toLocaleString()}↑ {stats.total_output_tokens.toLocaleString()}↓)</span>
              </span>
            )}
            {stats.avg_duration_ms > 0 && (
              <>
                <span className="text-gray-300">|</span>
                <span className="shrink-0">⏱ 均 {stats.avg_duration_ms < 1000 ? `${stats.avg_duration_ms}ms` : `${(stats.avg_duration_ms/1000).toFixed(1)}s`}</span>
              </>
            )}
          </div>
        )}

        {/* ── 清空确认弹窗 ── */}
        {showClearConfirm && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/20">
            <div className="bg-white rounded-xl shadow-xl p-5 mx-4 max-w-xs w-full">
              <div className="text-sm font-semibold text-gray-800 mb-2">清空审计记录？</div>
              <div className="text-xs text-gray-500 mb-4">将删除本 session 所有 trace / span / fixture，不可恢复。</div>
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    setClearing(true)
                    try {
                      await deleteSessionTraces(sessionId)
                      void loadTraces(sessionId)
                      void loadStats(sessionId)
                    } finally {
                      setClearing(false)
                      setShowClearConfirm(false)
                    }
                  }}
                  disabled={clearing}
                  className="flex-1 py-1.5 text-xs bg-red-500 text-white rounded-lg hover:bg-red-600 disabled:opacity-60 font-medium"
                >{clearing ? '删除中…' : '确认删除'}</button>
                <button
                  onClick={() => setShowClearConfirm(false)}
                  className="flex-1 py-1.5 text-xs bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 font-medium"
                >取消</button>
              </div>
            </div>
          </div>
        )}

        {/* ── 标签页切换 ── */}
        <div className="flex items-center gap-0 px-4 border-b border-gray-200 bg-white shrink-0">
          {(['audit', 'workflow'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`text-xs px-3 py-2 border-b-2 transition-colors font-medium ${
                activeTab === tab ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-400 hover:text-gray-600'
              }`}
            >
              {tab === 'audit' ? '🔍 链路审计' : '⚡ 工作流'}
            </button>
          ))}
          {/* 提炼成功提示 */}
          {extractDoneMsg && activeTab === 'audit' && (
            <span className="ml-auto text-[10px] text-emerald-600 animate-in fade-in duration-300 pr-1">
              {extractDoneMsg}
            </span>
          )}
        </div>

        {/* ── 工作流面板 ── */}
        {activeTab === 'workflow' && (
          <div className="flex-1 overflow-y-auto">
            <WorkflowPanel activeTraceId={selectedTraceId ?? undefined} />
          </div>
        )}

        {/* ── 审计主体：双栏 ── */}
        {activeTab === 'audit' && (
          <div className="flex flex-1 overflow-hidden">

            {/* 左栏：Trace 列表 */}
            <div className="w-[220px] shrink-0 border-r border-gray-100 flex flex-col overflow-hidden">

              {/* 层级筛选器 */}
              <HierarchyFilter
                onFilterChange={(wsId, projId, sessId) => setHierarchyFilter(wsId, projId, sessId)}
                currentWsId={filterWorkspaceId}
                currentProjId={filterProjectId}
                currentSessId={filterSessionId}
              />

              {/* 搜索 + domain 筛选 */}
              <div className="px-2 py-2 border-b border-gray-100 bg-gray-50 shrink-0 space-y-1.5">
                {/* 搜索框 */}
                <div className="relative">
                  <span className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-300 text-[10px]">🔍</span>
                  <input
                    type="text"
                    value={filterKeyword}
                    onChange={(e) => setFilterKeyword(e.target.value)}
                    placeholder="搜索消息…"
                    className="w-full text-[11px] border border-gray-200 rounded-md pl-5 pr-2 py-1
                               focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white"
                  />
                  {filterKeyword && (
                    <button
                      onClick={() => setFilterKeyword('')}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-300 hover:text-gray-500 text-xs"
                    >×</button>
                  )}
                </div>
                {/* Domain 筛选 */}
                <select
                  value={filterDomain}
                  onChange={(e) => setFilterDomain(e.target.value)}
                  className="w-full text-[11px] border border-gray-200 rounded-md px-2 py-1
                             focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white text-gray-600"
                >
                  {DOMAIN_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              {/* 列表 */}
              <div className="flex-1 overflow-y-auto">
                {loading && traces.length === 0 && (
                  <div className="flex items-center justify-center h-24 text-gray-400 text-xs">
                    <span className="animate-spin mr-1.5">⟳</span>加载中
                  </div>
                )}
                {error && <div className="p-3 text-xs text-red-500">{error}</div>}
                {!loading && !error && displayed.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-40 text-gray-400 text-xs space-y-1.5">
                    <span className="text-2xl">📭</span>
                    <span>{filterKeyword || filterDomain ? '无匹配记录' : '暂无记录'}</span>
                    <span className="text-[10px] text-gray-300 text-center px-4">发送消息后自动记录</span>
                  </div>
                )}
                {displayed.map(trace => (
                  <TraceListItem
                    key={trace.trace_id}
                    trace={trace}
                    isActive={selectedTraceId === trace.trace_id}
                    onClick={() => handleSelectTrace(trace.trace_id)}
                  />
                ))}

                {/* 加载更多 */}
                {hasMore && !filterKeyword && !filterDomain && (
                  <button
                    onClick={() => loadMore(filterSessionId || sessionId || undefined)}
                    disabled={loadingMore}
                    className="w-full py-2.5 text-[11px] text-blue-500 hover:text-blue-700 hover:bg-blue-50
                               transition-colors border-t border-gray-50 disabled:opacity-40"
                  >
                    {loadingMore ? <span className="animate-spin inline-block mr-1">⟳</span> : null}
                    {loadingMore ? '加载中…' : '加载更多'}
                  </button>
                )}
              </div>

              {/* 过滤结果统计 */}
              {(filterKeyword || filterDomain) && (
                <div className="px-3 py-1.5 border-t border-gray-100 bg-gray-50 shrink-0">
                  <span className="text-[10px] text-gray-400">
                    {displayed.length} / {traces.length} 条
                  </span>
                  <button
                    onClick={() => { setFilterKeyword(''); setFilterDomain('') }}
                    className="ml-2 text-[10px] text-blue-400 hover:text-blue-600"
                  >清除筛选</button>
                </div>
              )}
            </div>

            {/* 右栏：Span 时间线 + 详情 + 重播 */}
            <div className="flex-1 flex flex-col overflow-hidden">
              {!selectedTraceId ? (
                <div className="flex flex-col items-center justify-center h-full text-gray-400 text-sm space-y-2">
                  <span className="text-3xl">←</span>
                  <span>选择左侧记录查看链路</span>
                </div>
              ) : (
                <>
                  {/* Trace 概览条 */}
                  {activeTrace && (
                    <div className="px-4 py-2 border-b border-gray-100 bg-gray-50 shrink-0 flex items-center gap-3">
                      <span className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded font-bold">
                        {DOMAIN_LABEL[activeTrace.domain] ?? (activeTrace.domain || '未知')}
                      </span>
                      <span className="text-xs text-gray-700 truncate flex-1">{activeTrace.user_message}</span>
                      <span className="text-[10px] text-gray-400 font-mono shrink-0">
                        {activeTrace.total_steps}步 · {fmtMs(activeTrace.duration_ms)}
                      </span>
                      <span className={`text-[10px] font-medium shrink-0 ${TRACE_STATUS_COLOR[activeTrace.status] ?? 'text-gray-400'}`}>
                        {activeTrace.status}
                      </span>
                      <button
                        onClick={async () => {
                          try {
                            const blob = await exportSingleTrace(activeTrace.trace_id)
                            const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
                            downloadBlob(blob, `trace_${activeTrace.trace_id.slice(0, 8)}_${ts}.json`)
                          } catch (e) {
                            console.error('[exportTrace]', e)
                            alert(`导出失败：${e instanceof Error ? e.message : String(e)}`)
                          }
                        }}
                        title="导出当前 trace 的完整审计 JSON（含 raw_spans）"
                        className="shrink-0 flex items-center gap-1 text-[10px] text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded px-1.5 py-0.5 transition-colors"
                      >
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        导出
                      </button>
                    </div>
                  )}

                  {/* 内容区：上下分割 */}
                  <div className="flex-1 flex flex-col overflow-hidden">
                    {/* 上：Span 时间线 */}
                    <div className="border-b border-gray-100 shrink-0">
                      <div className="flex items-center justify-between px-4 py-1.5 bg-gray-50">
                        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">
                          工具调用链路 {spans.length > 0 && `(${spans.length})`}
                        </span>
                        {loading && <span className="text-[10px] text-blue-400 animate-pulse">加载中…</span>}
                      </div>
                      <div className="overflow-y-auto max-h-52 px-2 py-1">
                        {!loading && spans.length === 0 && (
                          <div className="text-xs text-gray-400 py-3 text-center">暂无工具调用记录</div>
                        )}
                        <SpanTimeline spans={spans} selectedIdx={selectedSpanIdx} onSelect={setSelectedSpanIdx} />
                      </div>
                    </div>

                    {/* 下：Span 详情 + 重播 + 提炼 */}
                    <div className="flex-1 overflow-hidden flex flex-col">
                      {selectedSpan ? (
                        <div className="flex-1 overflow-hidden">
                          <SpanDetail span={selectedSpan} />
                        </div>
                      ) : (
                        <div className="flex-1 flex items-center justify-center text-gray-400 text-xs">
                          点击上方步骤查看详情
                        </div>
                      )}

                      {/* 重播区 */}
                      {spans.length > 0 && <ReplayPanel traceId={selectedTraceId} />}

                      {/* 提炼为工作流（快捷入口） */}
                      {selectedTraceId && (
                        <ExtractButton
                          traceId={selectedTraceId}
                          onDone={(name) => {
                            setExtractDoneMsg(`✓ 已提炼「${name}」`)
                            setTimeout(() => setExtractDoneMsg(''), 4000)
                          }}
                        />
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* ── 底部状态栏 ── */}
        <div className="px-4 py-1.5 border-t border-gray-100 bg-gray-50 shrink-0 flex items-center justify-between">
          <span className="text-[10px] text-gray-400">本地 SQLite · 零侵入审计</span>
          <div className="flex items-center gap-2">
            {activeTab === 'audit' && stats && stats.total_input_tokens + stats.total_output_tokens > 0 && (
              <span className="text-[10px] text-purple-400 font-mono">
                {((stats.total_input_tokens + stats.total_output_tokens) / 1000).toFixed(1)}K tok
              </span>
            )}
            {activeTab === 'audit' && traces.length > 0 && (
              <span className="text-[10px] text-gray-300">{traces.length} 条</span>
            )}
            <span className="text-[10px] text-gray-300">v3.2</span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── PipelineTimelineButton：触发按钮 ────────────────────────────────────────

export function PipelineTimelineButton({ sessionId: _sessionId }: { sessionId: string }) {
  const { isOpen, togglePanel } = useTimelineStore()

  return (
    <button
      onClick={togglePanel}
      className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border font-medium transition-all
        ${isOpen
          ? 'bg-blue-500 text-white border-blue-500 shadow-sm'
          : 'bg-white text-gray-500 border-gray-200 hover:border-blue-300 hover:text-blue-600 hover:bg-blue-50'
        }`}
      title="查看工具调用审计链路 & 一键重播"
    >
      <span className="text-sm">🔍</span>
      <span>链路审计</span>
    </button>
  )
}
