'use client'

import { useScoreStore, type PipelineLog } from '@/entities/session/store'

// 工具名称 → 可读标签（与后端 tools/ 注册工具一一对应）
const TOOL_LABELS: Record<string, string> = {
  // abc_edit 分组（abc_tools.py + export_tools.py）
  transpose_abc:           '转调',
  change_tempo:            '调整速度',
  change_style:            '风格转换',
  add_ornament:            '添加装饰音',
  analyze_abc:             '分析谱子',
  get_abc_header:          '读取 Header',
  abc_to_sky_json:         '生成 Sky JSON',
  abc_to_midi_b64:         '生成 MIDI',
  // audio 分组（audio_tools.py）
  generate_audio_suno:     'Suno 生成音乐',
  get_suno_job_status:     '查询 Suno 任务',
  generate_lyrics_minimax: 'MiniMax 生成歌词',
  generate_audio_minimax:  'MiniMax 生成音乐',
  generate_cover_minimax:  'MiniMax 翻唱',
  abc_to_audio_prompt:     '提取音频 Prompt',
  // audio 分组（audio_evolve_tools.py）
  evolve_audio_prompt:     '进化音频 Prompt',
  diff_audio_params:       '对比生成参数',
  // audio 分组（voice_clone_tools.py）
  upload_voice_sample:     '上传音色样本',
  upload_prompt_audio:     '上传增强样本',
  clone_voice_minimax:     'MiniMax 克隆音色',
  list_cloned_voices:      '查询已克隆音色',
  synthesize_speech_minimax: '克隆音色合成语音',
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

/**
 * PipelineStatus - Pipeline 执行进度展示
 * 实时显示 Agent 工具调用卡片 + 步骤日志
 */
export function PipelineStatus() {
  const { pipelineLogs, streamingMessage, pipelineState } = useScoreStore()

  if (pipelineLogs.length === 0 && !streamingMessage) {
    return (
      <div className="px-4 py-3 text-xs text-gray-300 text-center">
        执行日志将在此显示
      </div>
    )
  }

  return (
    <div className="px-4 py-3 space-y-1.5 max-h-64 overflow-y-auto">
      {pipelineState === 'running' && (
        <div className="flex items-center gap-2 text-xs text-orange-500 mb-2">
          <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
          <span>Agent 执行中...</span>
        </div>
      )}

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
  )
}
