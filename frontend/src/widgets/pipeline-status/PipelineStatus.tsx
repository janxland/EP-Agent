'use client'

import { useState } from 'react'
import { useScoreStore, type PipelineLog } from '@/entities/session/store'

// 工具名称 → 可读标签（与后端 tools/ 注册工具一一对应）
const TOOL_LABELS: Record<string, string> = {
  // abc_edit 分组（abc_tools.py + export_tools.py）
  transpose_abc:           '转调',
  change_tempo:            '调整速度',
  change_style:            '风格转换',
  add_ornament:            '添加装饰音',
  analyze_abc:             '分析谱子',
  validate_abc:            '验证 ABC 音域',
  get_abc_header:          '读取 Header',
  abc_to_sky_json:         '生成 Sky JSON',
  abc_to_midi:             '生成 MIDI',
  abc_to_midi_b64:         '生成 MIDI',
  // workspace 分组
  list_workspace_files:    '列出工作区文件',
  read_workspace_files:    '批量读取文件',
  read_workspace_file:     '读取文件',
  write_workspace_file:    '写入文件',
  edit_workspace_file:     '编辑文件',
  // audio 分组（audio_tools.py）
  generate_audio_suno:     'Suno 生成音乐',
  get_suno_job_status:     '查询 Suno 任务',
  generate_lyrics_minimax: 'MiniMax 生成歌词',
  generate_audio_minimax:  'MiniMax 生成音乐',
  generate_cover_minimax:  'MiniMax 翻唱',
  abc_to_audio_prompt:     '提取音频 Prompt',
  evolve_audio_prompt:     '进化音频 Prompt',
  diff_audio_params:       '对比生成参数',
  upload_voice_sample:     '上传音色样本',
  upload_prompt_audio:     '上传增强样本',
  clone_voice_minimax:     'MiniMax 克隆音色',
  list_cloned_voices:      '查询已克隆音色',
  synthesize_speech_minimax: '克隆音色合成语音',
}

/**
 * 下载 JSON blob 到本地
 */
function _downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a   = document.createElement('a')
  a.href     = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * 导出 session 最近 N 条 trace 的完整审计链路 JSON
 * 调用后端 GET /api/sessions/{session_id}/traces/export?limit=N
 * 返回大模型友好格式：每条 trace 含完整 react_chain（入参/出参不截断）
 */
async function exportSessionTracesJson(sessionId: string, limit = 10): Promise<void> {
  if (!sessionId) return
  try {
    const res = await fetch(`/api/sessions/${sessionId}/traces/export?limit=${limit}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
    const blob = await res.blob()
    const ts   = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    _downloadBlob(blob, `session_${sessionId.slice(0, 8)}_traces_${ts}.json`)
  } catch (e) {
    console.error('[exportSessionTracesJson]', e)
    alert(`导出失败：${e instanceof Error ? e.message : String(e)}`)
  }
}

/**
 * 导出单条 trace 的完整审计链路 JSON
 * 调用后端 GET /api/traces/{trace_id}/export
 * 包含完整 react_chain + raw_spans，适合精确分析单次执行
 */
async function exportSingleTraceJson(traceId: string): Promise<void> {
  if (!traceId) return
  try {
    const res = await fetch(`/api/traces/${traceId}/export`)
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`)
    const blob = await res.blob()
    const ts   = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    _downloadBlob(blob, `trace_${traceId.slice(0, 8)}_${ts}.json`)
  } catch (e) {
    console.error('[exportSingleTraceJson]', e)
    alert(`导出失败：${e instanceof Error ? e.message : String(e)}`)
  }
}

/**
 * ToolCallCard - 工具调用卡片
 * 参考 magic-coding 的 ChatMessageItems 工具卡片设计
 */
function ToolCallCard({ log }: { log: PipelineLog }) {
  const label = TOOL_LABELS[log.toolName ?? ''] ?? log.toolName ?? '工具'
  const isRunning = log.status === 'running'
  const isFailed = log.status === 'failed'

  // 过滤掉 abc 参数（太长），只展示关键参数
  const displayArgs = Object.entries(log.toolArgs ?? {})
    .filter(([k]) => k !== 'abc')
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ')

  return (
    <div className={[
      'rounded-lg border px-3 py-2 text-xs font-mono',
      isRunning  ? 'border-orange-200 bg-orange-50/60'  : '',
      isFailed   ? 'border-red-200 bg-red-50/60'        : '',
      !isRunning && !isFailed ? 'border-gray-100 bg-gray-50/60' : '',
    ].join(' ')}>
      <div className="flex items-center gap-2">
        {isRunning && (
          <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin shrink-0" />
        )}
        {!isRunning && !isFailed && (
          <span className="text-green-500 shrink-0">✓</span>
        )}
        {isFailed && (
          <span className="text-red-500 shrink-0">✗</span>
        )}
        <span className={[
          'font-semibold',
          isRunning ? 'text-orange-600' : isFailed ? 'text-red-600' : 'text-gray-700',
        ].join(' ')}>
          {label}
        </span>
        {displayArgs && (
          <span className="text-gray-400 truncate max-w-[160px]">({displayArgs})</span>
        )}
      </div>
      {log.toolResult && !isRunning && (
        <div className="mt-1 text-gray-400 truncate pl-5">{log.toolResult}</div>
      )}
    </div>
  )
}

function LogItem({ log }: { log: PipelineLog }) {
  // 工具调用用专门的卡片
  if (log.type === 'tool_call') {
    return <ToolCallCard log={log} />
  }

  const icons: Record<string, string> = {
    step:     log.status === 'succeeded' ? '✓' : log.status === 'failed' ? '✗' : '◎',
    activity: '→',
    message:  '💬',
    error:    '⚠',
  }
  const colors: Record<string, string> = {
    step:     log.status === 'succeeded' ? 'text-green-600' : log.status === 'failed' ? 'text-red-500' : 'text-orange-500',
    activity: 'text-gray-500',
    message:  'text-gray-700',
    error:    'text-red-500',
  }

  return (
    <div className={`flex gap-2 text-xs ${colors[log.type] ?? 'text-gray-500'}`}>
      <span className="shrink-0 w-4 text-center">{icons[log.type] ?? '·'}</span>
      <span className="leading-relaxed">{log.text}</span>
    </div>
  )
}

/** 下载图标 SVG（复用） */
function DownloadIcon({ className = 'w-3 h-3' }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
    </svg>
  )
}

/**
 * PipelineStatus - Pipeline 执行进度展示
 * 实时显示 Agent 工具调用卡片 + 步骤日志
 * 右上角支持：
 *   - 「导出全部」：session 最近 10 条 trace（大模型友好 JSON，含完整入参/出参）
 *   - 「导出当前」：仅最新一条 trace（精确分析单次执行）
 */
export function PipelineStatus() {
  const { pipelineLogs, streamingMessage, pipelineState, sessionId } = useScoreStore()
  // 导出下拉菜单展开状态
  const [menuOpen, setMenuOpen] = useState(false)

  const hasLogs = pipelineLogs.length > 0 || !!streamingMessage

  if (!hasLogs && !sessionId) {
    return (
      <div className="px-4 py-3 text-xs text-gray-300 text-center">
        执行日志将在此显示
      </div>
    )
  }

  // 从日志中提取最新 trace_id（step 类型日志通常携带 traceId）
  const latestTraceId = (() => {
    for (let i = pipelineLogs.length - 1; i >= 0; i--) {
      const log = pipelineLogs[i] as PipelineLog & { traceId?: string }
      if (log.traceId) return log.traceId
    }
    return ''
  })()

  return (
    <div className="flex flex-col">
      {/* 顶栏：状态 + 导出按钮组 */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        {pipelineState === 'running' ? (
          <div className="flex items-center gap-2 text-xs text-orange-500">
            <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
            <span>Agent 执行中...</span>
          </div>
        ) : (
          <span className="text-xs text-gray-400">{hasLogs ? '执行日志' : '审计链路'}</span>
        )}

        {/* 导出按钮组（只要 sessionId 存在就显示） */}
        {sessionId && (
          <div className="relative flex items-center gap-1">
            {/* 主按钮：导出 session 全部 trace */}
            <button
              onClick={() => exportSessionTracesJson(sessionId, 10)}
              title="导出最近 10 条完整审计链路 JSON（大模型友好，含完整工具调用入参/出参）"
              className="flex items-center gap-1 text-xs text-gray-400 hover:text-blue-500 hover:bg-blue-50 rounded px-2 py-0.5 transition-colors"
            >
              <DownloadIcon />
              导出审计 JSON
            </button>
            {/* 下拉箭头：展开更多选项 */}
            <button
              onClick={() => setMenuOpen(v => !v)}
              title="更多导出选项"
              className="text-gray-300 hover:text-blue-400 px-0.5 py-0.5 rounded transition-colors"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {/* 下拉菜单 */}
            {menuOpen && (
              <div
                className="absolute right-0 top-6 z-20 bg-white border border-gray-100 rounded-lg shadow-lg py-1 min-w-[180px] text-xs"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <button
                  onClick={() => { exportSessionTracesJson(sessionId, 10); setMenuOpen(false) }}
                  className="w-full text-left px-3 py-1.5 hover:bg-blue-50 hover:text-blue-600 flex items-center gap-2"
                >
                  <DownloadIcon />
                  <span>最近 10 条 trace（推荐）</span>
                </button>
                <button
                  onClick={() => { exportSessionTracesJson(sessionId, 1); setMenuOpen(false) }}
                  className="w-full text-left px-3 py-1.5 hover:bg-blue-50 hover:text-blue-600 flex items-center gap-2"
                >
                  <DownloadIcon />
                  <span>仅最新 1 条 trace</span>
                </button>
                {latestTraceId && (
                  <button
                    onClick={() => { exportSingleTraceJson(latestTraceId); setMenuOpen(false) }}
                    className="w-full text-left px-3 py-1.5 hover:bg-blue-50 hover:text-blue-600 flex items-center gap-2"
                  >
                    <DownloadIcon />
                    <span>当前 trace（含 raw_spans）</span>
                  </button>
                )}
                <div className="border-t border-gray-100 mt-1 pt-1 px-3 py-1 text-gray-400">
                  JSON 可直接丢给大模型分析链路
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 日志列表 */}
      <div className="px-4 pb-3 space-y-1.5 max-h-64 overflow-y-auto">
        {pipelineLogs.map((log) => (
          <LogItem key={log.id} log={log} />
        ))}

        {streamingMessage && (
          <div className="flex gap-2 text-xs text-gray-700">
            <span className="shrink-0 w-4 text-center">💬</span>
            <span className="leading-relaxed">
              {streamingMessage}
              <span className="inline-block w-1 h-3 bg-gray-400 ml-0.5 animate-pulse" />
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
