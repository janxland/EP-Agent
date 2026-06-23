'use client'

import { useState, useEffect, useRef, memo } from 'react'

// ─── 工具图标配置（移植自 magic-coding，扩展 EP-Agent 音频工具）────────────────

interface ToolIconConfig { bg: string; emoji: string }

const TOOL_ICON_MAP: Record<string, ToolIconConfig> = {
  // 文件编辑（来自 coding）
  read_files:       { bg: '#3FCCFF22', emoji: '📄' },
  write_file:       { bg: '#285AFF22', emoji: '✏️' },
  edit_file:        { bg: '#00CEB922', emoji: '🖊️' },
  multi_edit_file:  { bg: '#00CEB922', emoji: '🖊️' },
  delete_files:     { bg: '#FF3F5922', emoji: '🗑️' },
  list_dir:         { bg: '#FD57B022', emoji: '📁' },
  file_search:      { bg: '#FFAF3F22', emoji: '🔍' },
  grep_search:      { bg: '#FF623F22', emoji: '🔎' },
  shell_exec:       { bg: '#341F8E22', emoji: '⚡' },
  // ABC 编辑工具
  transpose_abc:        { bg: '#F59E0B22', emoji: '🎵' },
  change_tempo:         { bg: '#F59E0B22', emoji: '🥁' },
  change_style:         { bg: '#8B5CF622', emoji: '🎨' },
  add_ornament:         { bg: '#10B98122', emoji: '🎶' },
  analyze_abc:          { bg: '#3B82F622', emoji: '🔬' },
  abc_to_sky_json:      { bg: '#06B6D422', emoji: '🎮' },
  abc_to_midi_b64:      { bg: '#6366F122', emoji: '🎹' },
  // 音频生成工具
  generate_audio_suno:      { bg: '#A855F722', emoji: '🎸' },
  generate_audio_minimax:   { bg: '#3B82F622', emoji: '🎼' },
  generate_cover_minimax:   { bg: '#EC489922', emoji: '🎤' },
  generate_lyrics_minimax:  { bg: '#F97316 22', emoji: '📝' },
  abc_to_audio_prompt:      { bg: '#14B8A622', emoji: '🔊' },
  evolve_audio_prompt:      { bg: '#8B5CF622', emoji: '🔄' },
  diff_audio_params:        { bg: '#6B728022', emoji: '📊' },
  // 音色克隆工具
  upload_voice_sample:        { bg: '#9333EA22', emoji: '🎙️' },
  upload_prompt_audio:        { bg: '#9333EA22', emoji: '🎙️' },
  clone_voice_minimax:        { bg: '#7C3AED22', emoji: '🧬' },
  list_cloned_voices:         { bg: '#6D28D922', emoji: '📋' },
  synthesize_speech_minimax:  { bg: '#5B21B622', emoji: '🔈' },
}

const TOOL_LABELS: Record<string, string> = {
  // 文件编辑
  read_files: '读取文件', write_file: '写入文件', edit_file: '编辑文件',
  multi_edit_file: '批量编辑', delete_files: '删除文件', list_dir: '列出目录',
  file_search: '搜索文件', grep_search: '内容检索', shell_exec: '执行命令',
  // ABC 编辑
  transpose_abc: '转调', change_tempo: '调整速度', change_style: '风格转换',
  add_ornament: '添加装饰音', analyze_abc: '分析谱子',
  abc_to_sky_json: '生成 Sky JSON', abc_to_midi_b64: '生成 MIDI',
  // 音频生成
  generate_audio_suno: 'Suno 生成音乐', generate_audio_minimax: 'MiniMax 生成音乐',
  generate_cover_minimax: 'MiniMax 翻唱', generate_lyrics_minimax: 'MiniMax 生成歌词',
  abc_to_audio_prompt: '提取音频 Prompt', evolve_audio_prompt: '进化音频 Prompt',
  diff_audio_params: '对比生成参数',
  // 音色克隆
  upload_voice_sample: '上传音色样本', upload_prompt_audio: '上传增强样本',
  clone_voice_minimax: 'MiniMax 克隆音色', list_cloned_voices: '查询已克隆音色',
  synthesize_speech_minimax: '克隆音色合成语音',
}

function getToolIcon(name: string): ToolIconConfig {
  return TOOL_ICON_MAP[name] ?? { bg: '#6B728022', emoji: '🔧' }
}

function getToolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}

// ─── JSON 格式化（容错）──────────────────────────────────────────────────────

function tryFormatJson(raw: string): string {
  if (!raw || raw === '{}') return 'NULL'
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

// ─── ToolCard 组件 ────────────────────────────────────────────────────────────

export interface ToolCardProps {
  toolName: string
  argumentsJson: string
  resultText?: string
  status: 'running' | 'succeeded' | 'failed'
  error?: string
}

export const ToolCard = memo(function ToolCard({
  toolName,
  argumentsJson,
  resultText,
  status,
  error,
}: ToolCardProps) {
  const [open, setOpen] = useState(status === 'running')
  const bodyRef = useRef<HTMLDivElement>(null)
  const icon = getToolIcon(toolName)
  const label = getToolLabel(toolName)
  const isRunning = status === 'running'
  const isFailed = status === 'failed'
  const displayArgs = tryFormatJson(argumentsJson)
  const displayResult = error ? `错误: ${error}` : tryFormatJson(resultText ?? '')

  // 流式时自动展开，完成后自动折叠（用户点击后不再自动）
  const userClickedRef = useRef(false)
  useEffect(() => {
    if (!userClickedRef.current) setOpen(isRunning)
  }, [isRunning])

  // 流式时滚到底
  useEffect(() => {
    if (open && isRunning && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [open, isRunning, argumentsJson])

  return (
    <div className={[
      'rounded-xl border text-xs transition-all overflow-hidden',
      isRunning ? 'border-orange-200 shadow-sm shadow-orange-100'
        : isFailed ? 'border-red-200'
        : 'border-gray-100',
      open ? 'w-full' : 'inline-flex w-fit max-w-full',
    ].join(' ')}>

      {/* 头部：图标 + 标签 + 工具名 + 折叠按钮 */}
      <div className={[
        'flex items-center gap-2 px-2.5 py-2 bg-white',
        open ? 'w-full' : 'w-fit max-w-full',
      ].join(' ')}>
        {/* 图标徽章 */}
        <span
          className="shrink-0 w-6 h-6 rounded-md flex items-center justify-center text-sm"
          style={{ background: icon.bg }}
        >
          {icon.emoji}
        </span>

        {/* 标签 + 工具名 */}
        <span className="font-medium text-gray-800 shrink-0">{label}</span>
        <span className="text-gray-400 truncate min-w-0">{toolName}</span>

        {/* 状态指示 */}
        <span className="shrink-0 ml-auto">
          {isRunning && (
            <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin inline-block" />
          )}
          {!isRunning && !isFailed && (
            <span className="text-green-500 font-bold">✓</span>
          )}
          {isFailed && (
            <span className="text-red-500 font-bold">✗</span>
          )}
        </span>

        {/* 折叠按钮 */}
        <button
          onClick={() => { userClickedRef.current = true; setOpen((o) => !o) }}
          className="shrink-0 w-5 h-5 rounded flex items-center justify-center text-gray-400 hover:bg-gray-100 transition-colors"
        >
          <svg className={['w-3 h-3 transition-transform', open ? 'rotate-90' : ''].join(' ')}
            fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      {/* 展开体：参数 + 结果 */}
      {open && (
        <div
          ref={bodyRef}
          className="px-2.5 pb-2.5 space-y-2 bg-gray-50/80 max-h-48 overflow-y-auto"
        >
          {/* 参数 */}
          <div className="space-y-1">
            <span className="text-gray-400 text-[10px] uppercase tracking-wider">参数</span>
            <pre className="bg-zinc-900 text-zinc-100 rounded-lg p-2 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words max-h-28 overflow-y-auto">
              {displayArgs}
              {isRunning && (
                <span className="inline-block w-px h-3 bg-zinc-100 ml-0.5 animate-pulse align-text-bottom" />
              )}
            </pre>
          </div>

          {/* 结果（完成后显示） */}
          {!isRunning && (resultText || error) && (
            <div className="space-y-1">
              <span className={['text-[10px] uppercase tracking-wider', isFailed ? 'text-red-400' : 'text-gray-400'].join(' ')}>
                {isFailed ? '错误' : '结果'}
              </span>
              <pre className={[
                'rounded-lg p-2 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words max-h-28 overflow-y-auto',
                isFailed ? 'bg-red-950 text-red-200' : 'bg-zinc-900 text-zinc-100',
              ].join(' ')}>
                {displayResult}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
})
