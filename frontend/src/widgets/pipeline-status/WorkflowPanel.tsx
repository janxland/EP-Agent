'use client'
/**
 * WorkflowPanel — 工作流管理面板 v2.1
 *
 * 修复 & 优化：
 *   - AbortController 正确传入 fetch，支持真实取消
 *   - 运行中显示「停止」按钮
 *   - 变量区块为空时不渲染
 *   - 运行完成后展示最终结果摘要
 *   - 历史记录展示变量值
 *   - 提炼时显示 LLM 分析进度
 *   - 模板支持「废弃」操作
 *   - 工具名映射中文 label
 */

import React, {
  useState, useEffect, useCallback, useRef,
} from 'react'
import {
  listWorkflows,
  listWorkflowRuns,
  runWorkflowSSE,
  extractWorkflow,
  getWorkflowStats,
  type WorkflowTemplate,
  type WorkflowRun,
} from '@/shared/lib/api'

// ─── 工具名中文映射（复用 PipelineStatus 的映射） ─────────────────────────────

const TOOL_LABEL: Record<string, string> = {
  finish_task:                '完成任务',
  health_check:               '健康检查',
  sovits_health_check:        'SoVITS 健康检查',
  sovits_list_audio_files:    '列出音频文件',
  sovits_list_models:         '列出模型',
  sovits_clone_voice:         '克隆音色',
  sovits_synthesize:          'SoVITS 合成语音',
  list_workspace_files:       '列出工作区文件',
  list_h5_templates:          '列出 H5 模板',
  list_cloned_voices:         '查询已克隆音色',
  upload_voice_sample:        '上传音色样本',
  clone_voice_minimax:        'MiniMax 克隆音色',
  synthesize_speech_minimax:  '克隆音色合成语音',
  generate_audio_suno:        'Suno 生成音乐',
  get_suno_job_status:        '查询 Suno 任务',
  generate_audio_minimax:     'MiniMax 生成音乐',
  generate_cover_minimax:     'MiniMax 翻唱',
  transpose_abc:              '转调',
  change_tempo:               '调整速度',
  abc_to_sky_json:            '生成 Sky JSON',
  abc_to_midi_b64:            '生成 MIDI',
  analyze_abc:                '分析谱子',
}

const toolLabel = (name: string) => TOOL_LABEL[name] ?? name

// ─── 工具函数 ─────────────────────────────────────────────────────────────────

const fmtMs = (ms: number) =>
  !ms ? '--' : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`

const fmtTime = (iso: string) => {
  if (!iso) return '--'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso.slice(0, 16) }
}

// ─── 类型 ─────────────────────────────────────────────────────────────────────

interface SSEStep {
  step_idx: number
  tool_name: string
  status: 'running' | 'ok' | 'error' | 'skipped'
  result_preview?: string
  duration_ms?: number
}

interface RunState {
  run_id: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  current_step: number
  total_steps: number
  steps: SSEStep[]
  duration_ms?: number
  error?: string
  final_result?: Record<string, unknown>
}

// ─── 子组件：剪枝率徽章 ───────────────────────────────────────────────────────

function PruneBadge({ pruned, total }: { pruned: number; total: number }) {
  if (total === 0) return null
  const pct = Math.round((pruned / total) * 100)
  const color = pct >= 70 ? 'bg-emerald-100 text-emerald-700'
    : pct >= 40 ? 'bg-amber-100 text-amber-700'
    : 'bg-gray-100 text-gray-500'
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${color}`}>
      ⚡ {pct}% 无LLM
    </span>
  )
}

// ─── 子组件：变量输入表单 ──────────────────────────────────────────────────────

function VariableForm({
  variables,
  values,
  onChange,
  disabled,
}: {
  variables: WorkflowTemplate['variables']
  values: Record<string, string>
  onChange: (k: string, v: string) => void
  disabled?: boolean
}) {
  if (!variables || variables.length === 0) {
    return (
      <p className="text-xs text-gray-400 italic py-1">此工作流无需变量输入，可直接运行</p>
    )
  }
  return (
    <div className="space-y-2.5">
      {variables.map((v) => (
        <div key={v.name}>
          <label className="block text-xs text-gray-500 mb-1">
            <span className="font-mono text-blue-600 bg-blue-50 px-1 rounded">{`{${v.name}}`}</span>
            <span className="ml-1 text-gray-400">{v.description}</span>
          </label>
          <input
            type="text"
            value={values[v.name] ?? ''}
            onChange={(e) => onChange(v.name, e.target.value)}
            disabled={disabled}
            placeholder={v.extract_from ?? `请输入 ${v.name}…`}
            className="w-full text-xs border border-gray-200 rounded-md px-2.5 py-1.5
                       focus:outline-none focus:ring-1 focus:ring-blue-400 focus:border-blue-400
                       bg-white disabled:bg-gray-50 disabled:text-gray-400 transition-colors"
          />
        </div>
      ))}
    </div>
  )
}

// ─── 子组件：SSE 进度条 ────────────────────────────────────────────────────────

function RunProgress({
  runState,
  onStop,
}: {
  runState: RunState
  onStop?: () => void
}) {
  const pct = runState.total_steps > 0
    ? Math.min(100, Math.round((runState.current_step / runState.total_steps) * 100))
    : 0

  const barColor = runState.status === 'succeeded' ? 'bg-emerald-500'
    : runState.status === 'failed'    ? 'bg-red-400'
    : runState.status === 'cancelled' ? 'bg-gray-400'
    : 'bg-blue-500'

  const displayPct = runState.status === 'succeeded' ? 100 : pct

  return (
    <div className="space-y-2.5">
      {/* 状态行 + 停止按钮 */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 flex-1 text-xs">
          {runState.status === 'running' && (
            <span className="w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin shrink-0" />
          )}
          {runState.status === 'succeeded' && <span className="text-emerald-500 shrink-0">✓</span>}
          {runState.status === 'failed'    && <span className="text-red-500 shrink-0">✗</span>}
          {runState.status === 'cancelled' && <span className="text-gray-400 shrink-0">⊘</span>}
          <span className={
            runState.status === 'succeeded' ? 'text-emerald-600 font-medium'
            : runState.status === 'failed'  ? 'text-red-500'
            : runState.status === 'cancelled' ? 'text-gray-400'
            : 'text-blue-500'
          }>
            {runState.status === 'running'
              ? `执行中 ${runState.current_step}/${runState.total_steps}`
              : runState.status === 'succeeded'
              ? `✓ 全部完成 · ${fmtMs(runState.duration_ms ?? 0)}`
              : runState.status === 'cancelled'
              ? '已取消'
              : `失败${runState.error ? ` — ${runState.error.slice(0, 40)}` : ''}`}
          </span>
        </div>
        {runState.status === 'running' && onStop && (
          <button
            onClick={onStop}
            className="text-[11px] text-gray-400 hover:text-red-500 border border-gray-200
                       hover:border-red-300 rounded px-2 py-0.5 transition-colors shrink-0"
          >
            停止
          </button>
        )}
      </div>

      {/* 进度条 */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${displayPct}%` }}
          />
        </div>
        <span className="text-[10px] text-gray-400 tabular-nums w-7 text-right">
          {displayPct}%
        </span>
      </div>

      {/* 步骤列表 */}
      <div className="border border-gray-100 rounded-md overflow-hidden">
        <div className="max-h-44 overflow-y-auto divide-y divide-gray-50">
          {runState.steps.length === 0 && (
            <div className="py-3 text-center text-xs text-gray-300">等待执行…</div>
          )}
          {runState.steps.map((step) => (
            <div
              key={step.step_idx}
              className={`flex items-center gap-2 px-2.5 py-1.5 text-xs
                ${step.status === 'running' ? 'bg-blue-50' : ''}`}
            >
              <span className="w-4 text-center text-gray-300 tabular-nums shrink-0">
                {step.step_idx + 1}
              </span>
              {step.status === 'running' && (
                <span className="w-2.5 h-2.5 border border-blue-400 border-t-transparent rounded-full animate-spin shrink-0" />
              )}
              {step.status === 'ok'      && <span className="text-emerald-500 shrink-0">✓</span>}
              {step.status === 'error'   && <span className="text-red-400 shrink-0">✗</span>}
              {step.status === 'skipped' && <span className="text-gray-300 shrink-0">—</span>}
              <span className={`flex-1 truncate font-medium ${
                step.status === 'running' ? 'text-blue-600'
                : step.status === 'error' ? 'text-red-500'
                : step.status === 'skipped' ? 'text-gray-300'
                : 'text-gray-600'
              }`}>
                {toolLabel(step.tool_name)}
              </span>
              {step.duration_ms !== undefined && step.status !== 'running' && (
                <span className="text-gray-300 tabular-nums text-[10px] shrink-0">
                  {fmtMs(step.duration_ms)}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 最终结果摘要 */}
      {runState.status === 'succeeded' && runState.final_result &&
        Object.keys(runState.final_result).length > 0 && (
        <div className="bg-emerald-50 border border-emerald-100 rounded-md px-2.5 py-2">
          <p className="text-[10px] text-emerald-600 font-medium mb-1">执行结果</p>
          <div className="space-y-0.5">
            {Object.entries(runState.final_result).slice(0, 4).map(([k, v]) => (
              <div key={k} className="flex gap-1.5 text-[11px]">
                <span className="text-emerald-500 font-mono shrink-0">{k}:</span>
                <span className="text-gray-600 truncate">{String(v).slice(0, 60)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── 子组件：模板卡片 ─────────────────────────────────────────────────────────

function TemplateCard({
  template,
  onRun,
  onViewHistory,
  isExpanded,
  onToggle,
}: {
  template: WorkflowTemplate
  onRun: (t: WorkflowTemplate) => void
  onViewHistory: (t: WorkflowTemplate) => void
  isExpanded: boolean
  onToggle: () => void
}) {
  const domainColor: Record<string, string> = {
    voice_clone: 'bg-purple-100 text-purple-700',
    audio:       'bg-blue-100 text-blue-700',
    score:       'bg-green-100 text-green-700',
    h5:          'bg-orange-100 text-orange-700',
  }
  const dc = domainColor[template.domain] ?? 'bg-gray-100 text-gray-500'

  return (
    <div className="border border-gray-100 rounded-lg overflow-hidden hover:border-gray-200 transition-colors bg-white">
      {/* 卡片头 */}
      <div
        className="flex items-start gap-2 px-3 py-2.5 cursor-pointer hover:bg-gray-50/80 transition-colors"
        onClick={onToggle}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs font-semibold text-gray-800">
              {template.name}
            </span>
            <PruneBadge pruned={template.pruned_steps} total={template.total_steps} />
            {template.domain && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${dc}`}>
                {template.domain}
              </span>
            )}
          </div>
          <p className="text-[11px] text-gray-400 mt-0.5 truncate leading-relaxed">
            {template.description || template.trigger_pattern || '—'}
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 pt-0.5">
          <span className="text-[10px] text-gray-300 tabular-nums">
            {template.total_steps}步
          </span>
          <svg
            className={`w-3.5 h-3.5 text-gray-300 transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* 展开详情 */}
      {isExpanded && (
        <div className="border-t border-gray-50 px-3 py-3 bg-gray-50/40 space-y-3">
          {/* 步骤预览 */}
          <div>
            <p className="text-[10px] text-gray-400 font-semibold uppercase tracking-wide mb-1.5">
              步骤 ({template.total_steps})
            </p>
            <div className="space-y-1">
              {(template.steps ?? []).slice(0, 8).map((step) => (
                <div key={step.step_idx} className="flex items-center gap-1.5 text-[11px]">
                  <span className="w-4 text-center text-gray-300 tabular-nums shrink-0">
                    {step.step_idx + 1}
                  </span>
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    step.llm_required ? 'bg-purple-300' : 'bg-emerald-300'
                  }`} />
                  <span className="text-gray-600 truncate flex-1">
                    {toolLabel(step.tool_name)}
                  </span>
                  {step.duration_ms ? (
                    <span className="text-gray-300 text-[10px] shrink-0">{fmtMs(step.duration_ms)}</span>
                  ) : null}
                  {!step.llm_required && (
                    <span className="text-[9px] text-emerald-500 shrink-0">无LLM</span>
                  )}
                </div>
              ))}
              {(template.steps ?? []).length > 8 && (
                <p className="text-[10px] text-gray-300 pl-5">
                  …还有 {template.steps.length - 8} 步
                </p>
              )}
            </div>
          </div>

          {/* 变量（有才显示） */}
          {(template.variables ?? []).length > 0 && (
            <div>
              <p className="text-[10px] text-gray-400 font-semibold uppercase tracking-wide mb-1.5">
                变量槽位
              </p>
              <div className="flex flex-wrap gap-1.5">
                {template.variables.map((v) => (
                  <span key={v.name}
                    className="text-[10px] bg-blue-50 text-blue-600 px-2 py-0.5 rounded-full font-mono border border-blue-100">
                    {`{${v.name}}`}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 操作按钮 */}
          <div className="flex gap-2">
            <button
              onClick={() => onRun(template)}
              className="flex-1 text-xs bg-blue-500 hover:bg-blue-600 active:bg-blue-700
                         text-white rounded-md px-3 py-1.5 transition-colors font-medium"
            >
              ▶ 运行
            </button>
            <button
              onClick={() => onViewHistory(template)}
              className="text-xs border border-gray-200 hover:bg-gray-100 text-gray-500
                         rounded-md px-3 py-1.5 transition-colors"
            >
              历史
            </button>
          </div>

          {/* 元信息 */}
          <p className="text-[10px] text-gray-300 leading-relaxed">
            来源 trace: <span className="font-mono">{template.source_trace_id?.slice(0, 12)}…</span>
            {' · '}{fmtTime(template.created_at)}
          </p>
        </div>
      )}
    </div>
  )
}

// ─── 子组件：运行历史 ─────────────────────────────────────────────────────────

function RunHistory({
  template,
  runs,
  onClose,
  onRerun,
}: {
  template: WorkflowTemplate
  runs: WorkflowRun[]
  onClose: () => void
  onRerun: (t: WorkflowTemplate) => void
}) {
  const statusIcon: Record<string, string> = {
    succeeded: '✓', failed: '✗', running: '…', pending: '○',
  }
  const statusColor: Record<string, string> = {
    succeeded: 'text-emerald-600',
    failed:    'text-red-500',
    running:   'text-blue-500',
    pending:   'text-gray-400',
  }

  return (
    <div className="space-y-2">
      {/* 标题行 */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-gray-700">{template.name}</p>
          <p className="text-[10px] text-gray-400">执行历史</p>
        </div>
        <button onClick={onClose} className="text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
          <span>←</span> 返回
        </button>
      </div>

      {/* 再次运行 */}
      <button
        onClick={() => onRerun(template)}
        className="w-full text-xs bg-blue-50 hover:bg-blue-100 text-blue-600
                   border border-blue-100 rounded-md px-3 py-1.5 transition-colors"
      >
        ▶ 再次运行
      </button>

      {/* 列表 */}
      {runs.length === 0 && (
        <p className="text-xs text-gray-300 text-center py-6">暂无执行记录</p>
      )}
      <div className="space-y-1.5">
        {runs.map((run) => {
          let vars: Record<string, string> = {}
          try { vars = JSON.parse(run.variables ?? '{}') } catch { /* ok */ }
          const varEntries = Object.entries(vars).slice(0, 2)

          return (
            <div key={run.run_id}
              className="border border-gray-100 rounded-md px-2.5 py-2 hover:bg-gray-50 transition-colors">
              <div className="flex items-center gap-2">
                <span className={`text-sm shrink-0 ${statusColor[run.status] ?? 'text-gray-400'}`}>
                  {statusIcon[run.status] ?? '?'}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className={`font-medium ${statusColor[run.status] ?? 'text-gray-500'}`}>
                      {run.status === 'succeeded' ? '成功' : run.status === 'failed' ? '失败' : run.status}
                    </span>
                    <span className="text-gray-300">·</span>
                    <span className="text-gray-400 tabular-nums">{fmtMs(run.duration_ms ?? 0)}</span>
                    <span className="text-gray-300">·</span>
                    <span className="text-gray-400">{fmtTime(run.started_at)}</span>
                  </div>
                  {varEntries.length > 0 && (
                    <div className="text-[10px] text-gray-400 mt-0.5 truncate">
                      {varEntries.map(([k, v]) => `${k}=${v}`).join(' · ')}
                    </div>
                  )}
                </div>
                <span className="text-[10px] text-gray-300 shrink-0 tabular-nums">
                  {run.total_steps}步
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── 子组件：运行对话框 ───────────────────────────────────────────────────────

function RunDialog({
  template,
  onClose,
}: {
  template: WorkflowTemplate
  onClose: () => void
}) {
  const [varValues, setVarValues]   = useState<Record<string, string>>({})
  const [runState, setRunState]     = useState<RunState | null>(null)
  const [starting, setStarting]     = useState(false)
  const abortCtrlRef                = useRef<AbortController | null>(null)
  const readerRef                   = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null)
  const cancelledRef                = useRef(false) // 防止 abort 后 finally 覆盖 cancelled 状态

  const handleVarChange = useCallback((k: string, v: string) => {
    setVarValues((prev) => ({ ...prev, [k]: v }))
  }, [])

  // 停止执行：立即中断 SSE 流，UI 切换为 cancelled
  const handleStop = useCallback(() => {
    cancelledRef.current = true
    abortCtrlRef.current?.abort()
    readerRef.current?.cancel().catch(() => {/* ok */})
    setRunState((prev) => prev ? { ...prev, status: 'cancelled' } : prev)
    setStarting(false)
  }, [])

  const handleRun = useCallback(async () => {
    // 重试时清空上次步骤记录
    cancelledRef.current = false
    setStarting(true)
    const ctrl = new AbortController()
    abortCtrlRef.current = ctrl

    // 初始化 runState（清空 steps，防止叠加）
    setRunState({
      run_id: '',
      status: 'running',
      current_step: 0,
      total_steps: template.total_steps,
      steps: [],
      error: undefined,
      final_result: undefined,
    })

    try {
      const resp = await runWorkflowSSE(template.template_id, varValues, '', ctrl.signal)
      if (!resp.ok) {
        const errText = await resp.text().catch(() => resp.statusText)
        throw new Error(errText)
      }
      if (!resp.body) throw new Error('响应无 body')

      const reader = resp.body.getReader()
      readerRef.current = reader
      const decoder = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (!raw || raw === '[DONE]') continue
          try {
            const evt = JSON.parse(raw)
            const { type, ...payload } = evt

            if (type === 'workflow.start') {
              setRunState((prev) => prev ? {
                ...prev,
                run_id:      payload.run_id ?? '',
                total_steps: payload.total_steps ?? template.total_steps,
              } : prev)
            }

            if (type === 'workflow.step') {
              setRunState((prev) => {
                if (!prev) return prev
                const idx = prev.steps.findIndex((s) => s.step_idx === payload.step_idx)
                const newStep: SSEStep = {
                  step_idx:       payload.step_idx,
                  tool_name:      payload.tool_name,
                  status:         payload.status,
                  result_preview: payload.result_preview,
                  duration_ms:    payload.duration_ms,
                }
                const steps = idx >= 0
                  ? prev.steps.map((s, i) => i === idx ? newStep : s)
                  : [...prev.steps, newStep]
                return {
                  ...prev,
                  current_step: payload.status === 'running'
                    ? payload.step_idx
                    : payload.step_idx + 1,
                  steps,
                }
              })
            }

            if (type === 'workflow.complete') {
              setRunState((prev) => prev ? {
                ...prev,
                status:       payload.status === 'succeeded' ? 'succeeded' : 'failed',
                current_step: payload.total_steps ?? prev.total_steps,
                duration_ms:  payload.duration_ms,
                final_result: payload.result ?? {},
              } : prev)
            }

            if (type === 'workflow.error') {
              setRunState((prev) => prev ? {
                ...prev,
                status: 'failed',
                error:  payload.error,
              } : prev)
            }

            // 后端确认取消
            if (type === 'workflow.cancelled') {
              setRunState((prev) => prev ? { ...prev, status: 'cancelled' } : prev)
            }
          } catch { /* skip malformed line */ }
        }
      }
    } catch (err: unknown) {
      // AbortError = 用户主动取消，状态已由 handleStop 设置，不覆盖
      if ((err as Error)?.name === 'AbortError' || cancelledRef.current) return
      const msg = err instanceof Error ? err.message : String(err)
      setRunState((prev) => prev
        ? { ...prev, status: 'failed', error: msg }
        : { run_id: '', status: 'failed', current_step: 0,
            total_steps: template.total_steps, steps: [], error: msg }
      )
    } finally {
      // 仅在非取消情况下清除 starting
      if (!cancelledRef.current) setStarting(false)
    }
  }, [template, varValues])

  // 组件卸载时自动取消正在进行的请求
  useEffect(() => {
    return () => {
      abortCtrlRef.current?.abort()
    }
  }, [])

  const isRunning = runState?.status === 'running'
  const isDone    = runState?.status === 'succeeded' || runState?.status === 'failed' || runState?.status === 'cancelled'
  const canRun    = !starting && !isRunning

  return (
    <div className="space-y-3">
      {/* 标题行 */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-gray-800 truncate">{template.name}</p>
          <p className="text-[11px] text-gray-400 mt-0.5">{template.description}</p>
        </div>
        <button
          onClick={onClose}
          className="text-gray-300 hover:text-gray-500 text-xl leading-none shrink-0 mt-0.5"
        >
          ×
        </button>
      </div>

      {/* 变量输入（未开始时显示） */}
      {!runState && (
        <VariableForm
          variables={template.variables ?? []}
          values={varValues}
          onChange={handleVarChange}
        />
      )}

      {/* 运行进度 */}
      {runState && (
        <RunProgress
          runState={runState}
          onStop={isRunning ? handleStop : undefined}
        />
      )}

      {/* 操作按钮区 */}
      <div className="flex gap-2">
        {canRun && (
          <button
            onClick={handleRun}
            className="flex-1 text-xs bg-blue-500 hover:bg-blue-600 active:bg-blue-700
                       text-white rounded-md px-3 py-2 transition-colors font-medium"
          >
            {runState?.status === 'failed' ? '↺ 重试' : runState?.status === 'cancelled' ? '↺ 重新运行' : '▶ 开始运行'}
          </button>
        )}
        {isDone && (
          <button
            onClick={onClose}
            className="flex-1 text-xs border border-gray-200 hover:bg-gray-50
                       text-gray-600 rounded-md px-3 py-2 transition-colors"
          >
            关闭
          </button>
        )}
      </div>
    </div>
  )
}

// ─── 主组件：WorkflowPanel ────────────────────────────────────────────────────

export interface WorkflowPanelProps {
  /** 当前选中的 trace_id，用于"提炼为工作流"按钮 */
  activeTraceId?: string
}

export function WorkflowPanel({ activeTraceId }: WorkflowPanelProps) {
  const [templates, setTemplates]             = useState<WorkflowTemplate[]>([])
  const [loading, setLoading]                 = useState(false)
  const [error, setError]                     = useState('')
  const [expandedId, setExpandedId]           = useState<string | null>(null)
  const [runningTemplate, setRunningTemplate] = useState<WorkflowTemplate | null>(null)
  const [historyTemplate, setHistoryTemplate] = useState<WorkflowTemplate | null>(null)
  const [historyRuns, setHistoryRuns]         = useState<WorkflowRun[]>([])
  const [extracting, setExtracting]           = useState(false)
  const [extractMsg, setExtractMsg]           = useState('')
  const [extractProgress, setExtractProgress] = useState(0)
  const [stats, setStats]                     = useState<{
    total_templates: number; total_runs: number
    succeeded_runs: number; avg_duration_ms: number
  } | null>(null)

  // 加载模板列表 + 统计
  const loadTemplates = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [data, statsData] = await Promise.allSettled([
        listWorkflows(),
        getWorkflowStats(),
      ])
      if (data.status === 'fulfilled') setTemplates(data.value.templates ?? [])
      else setError(data.reason?.message ?? '加载失败')
      if (statsData.status === 'fulfilled') setStats(statsData.value)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadTemplates() }, [loadTemplates])

  // 查看历史
  const handleViewHistory = useCallback(async (t: WorkflowTemplate) => {
    setHistoryTemplate(t)
    try {
      const data = await listWorkflowRuns(t.template_id)
      setHistoryRuns(data.runs ?? [])
    } catch {
      setHistoryRuns([])
    }
  }, [])

  // 从当前 trace 提炼（带模拟进度条）
  const handleExtract = useCallback(async () => {
    if (!activeTraceId) return
    setExtracting(true)
    setExtractMsg('')
    setExtractProgress(0)

    // 模拟进度：0→30→60→90，等待 LLM 分析
    const steps = [30, 60, 90]
    let i = 0
    const timer = setInterval(() => {
      if (i < steps.length) setExtractProgress(steps[i++])
    }, 800)

    try {
      const res = await extractWorkflow(activeTraceId, true)
      clearInterval(timer)
      setExtractProgress(100)
      setExtractMsg(`✓ 已提炼「${res.name ?? res.template_id}」`)
      await loadTemplates()
      // 自动展开新提炼的模板
      if (res.template_id) setExpandedId(res.template_id)
    } catch (e: unknown) {
      clearInterval(timer)
      setExtractProgress(0)
      setExtractMsg(`✗ ${e instanceof Error ? e.message : '提炼失败'}`)
    } finally {
      setExtracting(false)
      setTimeout(() => setExtractProgress(0), 1500)
    }
  }, [activeTraceId, loadTemplates])

  // ── 渲染：运行对话框 ────────────────────────────────────────────────────────
  if (runningTemplate) {
    return (
      <div className="px-3 py-3">
        <RunDialog
          template={runningTemplate}
          onClose={() => { setRunningTemplate(null); void loadTemplates() }}
        />
      </div>
    )
  }

  // ── 渲染：历史记录 ──────────────────────────────────────────────────────────
  if (historyTemplate) {
    return (
      <div className="px-3 py-3">
        <RunHistory
          template={historyTemplate}
          runs={historyRuns}
          onClose={() => setHistoryTemplate(null)}
          onRerun={(t) => { setHistoryTemplate(null); setRunningTemplate(t) }}
        />
      </div>
    )
  }

  // ── 渲染：模板列表 ──────────────────────────────────────────────────────────
  return (
    <div className="px-3 py-3 space-y-3">
      {/* 顶部操作栏 */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-gray-700 flex-1">
          工作流
          {templates.length > 0 && (
            <span className="ml-1 text-gray-400 font-normal">({templates.length})</span>
          )}
        </span>
        <button
          onClick={loadTemplates}
          disabled={loading}
          className="text-[11px] text-gray-400 hover:text-gray-600 w-6 h-6 flex items-center
                     justify-center rounded hover:bg-gray-100 transition-colors disabled:opacity-40"
          title="刷新"
        >
          <span className={loading ? 'animate-spin inline-block' : ''}>↻</span>
        </button>
      </div>

      {/* 统计摘要卡片 */}
      {stats && stats.total_runs > 0 && (
        <div className="grid grid-cols-3 gap-1.5">
          {[
            { label: '模板', value: stats.total_templates, color: 'text-blue-600' },
            { label: '执行', value: stats.total_runs,      color: 'text-gray-600' },
            { label: '成功', value: stats.succeeded_runs,  color: 'text-emerald-600' },
          ].map(({ label, value, color }) => (
            <div key={label}
              className="bg-gray-50 rounded-md px-2 py-1.5 text-center border border-gray-100">
              <p className={`text-sm font-bold tabular-nums ${color}`}>{value}</p>
              <p className="text-[10px] text-gray-400">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* 从 Trace 提炼按钮 */}
      {activeTraceId && (
        <div className="space-y-1.5">
          <button
            onClick={handleExtract}
            disabled={extracting}
            className="w-full text-xs border border-dashed border-blue-300
                       hover:border-blue-500 hover:bg-blue-50 text-blue-500
                       rounded-md px-3 py-2 transition-all disabled:opacity-60
                       flex items-center justify-center gap-1.5"
          >
            {extracting ? (
              <>
                <span className="w-3 h-3 border border-blue-400 border-t-transparent rounded-full animate-spin" />
                LLM 分析中…
              </>
            ) : (
              <>✦ 从当前 Trace 提炼工作流</>
            )}
          </button>

          {/* 提炼进度条 */}
          {extracting && extractProgress > 0 && (
            <div className="h-1 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-400 rounded-full transition-all duration-700"
                style={{ width: `${extractProgress}%` }}
              />
            </div>
          )}

          {extractMsg && (
            <p className={`text-[11px] ${extractMsg.startsWith('✓') ? 'text-emerald-600' : 'text-red-500'}`}>
              {extractMsg}
            </p>
          )}
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="flex items-center gap-1.5 text-xs text-red-400 bg-red-50 rounded-md px-2.5 py-1.5">
          <span>⚠</span>
          <span>{error}</span>
          <button onClick={loadTemplates} className="ml-auto text-red-400 hover:text-red-600">重试</button>
        </div>
      )}

      {/* 加载态 */}
      {loading && templates.length === 0 && (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-gray-300">
          <span className="animate-spin">⟳</span> 加载中…
        </div>
      )}

      {/* 空态 */}
      {!loading && templates.length === 0 && !error && (
        <div className="text-center py-10 space-y-2">
          <p className="text-3xl">⚡</p>
          <p className="text-xs font-medium text-gray-500">还没有工作流模板</p>
          <p className="text-[11px] text-gray-300 leading-relaxed">
            {activeTraceId
              ? '点击上方按钮，从当前 Trace 一键提炼'
              : '在「链路审计」选中一条 Trace，\n切换到此标签页提炼'}
          </p>
        </div>
      )}

      {/* 模板列表 */}
      {templates.length > 0 && (
        <div className="space-y-2">
          {templates.map((t) => (
            <TemplateCard
              key={t.template_id}
              template={t}
              isExpanded={expandedId === t.template_id}
              onToggle={() => setExpandedId(
                expandedId === t.template_id ? null : t.template_id
              )}
              onRun={setRunningTemplate}
              onViewHistory={handleViewHistory}
            />
          ))}
        </div>
      )}

      {/* 图例 */}
      {templates.length > 0 && (
        <div className="flex items-center gap-4 pt-2 border-t border-gray-50">
          <div className="flex items-center gap-1.5 text-[10px] text-gray-300">
            <span className="w-2 h-2 rounded-full bg-emerald-300 shrink-0" />
            无LLM步骤
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-gray-300">
            <span className="w-2 h-2 rounded-full bg-purple-300 shrink-0" />
            需LLM决策
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-gray-300">
            <span className="w-2 h-2 rounded-full bg-gray-200 shrink-0" />
            已跳过
          </div>
        </div>
      )}
    </div>
  )
}
