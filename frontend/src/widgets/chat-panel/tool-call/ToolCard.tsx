'use client'

import { useState, useEffect, useRef, memo } from 'react'

// ─── 工具图标配置 ─────────────────────────────────────────────────────────────

interface ToolIconConfig { bg: string; fg: string; emoji: string }

const TOOL_ICON_MAP: Record<string, ToolIconConfig> = {
  // ── Universal Runner 路由工具 ──────────────────────────────────────────────
  intent_router:      { bg: '#FFF7ED', fg: '#EA580C', emoji: '🧭' },
  convert_sky_json:   { bg: '#ECFEFF', fg: '#0891B2', emoji: '🎮' },
  abc_editor:         { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️' },
  abc_composer:       { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🎵' },
  audio_generator:    { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎧' },
  voice_clone:        { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🎤' },
  h5_generator:       { bg: '#FFF1F2', fg: '#E11D48', emoji: '🎨' },
  // ── 工作区工具 ────────────────────────────────────────────────────────────
  list_workspace_files:    { bg: '#F0FDF4', fg: '#16A34A', emoji: '📂' },
  read_workspace_file:     { bg: '#EFF6FF', fg: '#2563EB', emoji: '📄' },
  write_workspace_file:    { bg: '#FFF7ED', fg: '#EA580C', emoji: '💾' },
  delete_workspace_file:   { bg: '#FFF1F2', fg: '#E11D48', emoji: '🗑️' },
  get_workspace_file_url:  { bg: '#F0FDF4', fg: '#16A34A', emoji: '🔗' },
  copy_workspace_file:     { bg: '#ECFEFF', fg: '#0891B2', emoji: '📋' },
  rename_workspace_file:   { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️' },
  move_workspace_file:     { bg: '#EDE9FE', fg: '#7C3AED', emoji: '📦' },
  // ── H5 工具 ───────────────────────────────────────────────────────────────
  list_h5_templates:       { bg: '#FFF1F2', fg: '#E11D48', emoji: '🖼️' },
  generate_h5_poster:      { bg: '#FFF1F2', fg: '#E11D48', emoji: '🎨' },
  generate_h5_from_abc:    { bg: '#FFF1F2', fg: '#BE123C', emoji: '🎵' },
  generate_h5_from_midi:   { bg: '#FFF1F2', fg: '#BE123C', emoji: '🎹' },
  save_h5_file:            { bg: '#F0FDF4', fg: '#15803D', emoji: '💾' },
  finish_task:             { bg: '#F0FDF4', fg: '#15803D', emoji: '✅' },
  // ── ABC 编辑工具 ──────────────────────────────────────────────────────────
  transpose_abc:      { bg: '#FFFBEB', fg: '#D97706', emoji: '🎵' },
  change_tempo:       { bg: '#FFFBEB', fg: '#B45309', emoji: '🥁' },
  change_style:       { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🎨' },
  add_ornament:       { bg: '#F0FDF4', fg: '#15803D', emoji: '🎶' },
  analyze_abc:        { bg: '#EFF6FF', fg: '#1D4ED8', emoji: '🔬' },
  abc_to_sky_json:    { bg: '#ECFEFF', fg: '#0E7490', emoji: '🎮' },
  abc_to_midi_b64:    { bg: '#EEF2FF', fg: '#4338CA', emoji: '🎹' },
  // ── 音频生成工具 ──────────────────────────────────────────────────────────
  generate_audio_suno:     { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎸' },
  generate_audio_minimax:  { bg: '#EFF6FF', fg: '#2563EB', emoji: '🎼' },
  generate_cover_minimax:  { bg: '#FDF2F8', fg: '#C026D3', emoji: '🎤' },
  generate_lyrics_minimax: { bg: '#FFF7ED', fg: '#EA580C', emoji: '📝' },
  abc_to_audio_prompt:     { bg: '#F0FDFA', fg: '#0D9488', emoji: '🔊' },
  evolve_audio_prompt:     { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🔄' },
  diff_audio_params:       { bg: '#F9FAFB', fg: '#374151', emoji: '📊' },
  // ── 音色克隆工具（MiniMax）─────────────────────────────────────────────────
  upload_voice_sample:       { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎙️' },
  clone_voice_minimax:       { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🧬' },
  list_cloned_voices:        { bg: '#EDE9FE', fg: '#5B21B6', emoji: '📋' },
  synthesize_speech_minimax: { bg: '#EDE9FE', fg: '#4C1D95', emoji: '🔈' },
  // ── GPT-SoVITS 工具 ───────────────────────────────────────────────────────
  voice_clone_router:        { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🎙️' },
  sovits_health_check:       { bg: '#F0FDF4', fg: '#16A34A', emoji: '💚' },
  sovits_tts_and_save:       { bg: '#EDE9FE', fg: '#7C3AED', emoji: '🔊' },
  sovits_clone_and_save:     { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🧬' },
  sovits_list_models:        { bg: '#EFF6FF', fg: '#2563EB', emoji: '📋' },
  sovits_set_model:          { bg: '#FFFBEB', fg: '#D97706', emoji: '🔄' },
  sovits_list_audio_files:   { bg: '#F0FDF4', fg: '#15803D', emoji: '🎵' },
  upload_prompt_audio:       { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎤' },
}

const TOOL_LABELS: Record<string, string> = {
  intent_router:    '意图识别',
  convert_sky_json: '解析 Sky 谱子',
  abc_editor:       'ABC 谱子编辑',
  abc_composer:     'ABC 谱子创作',
  audio_generator:  '音频生成',
  voice_clone:      '音色克隆',
  h5_generator:     'H5 海报生成',
  // 工作区
  list_workspace_files:    '列出工作区文件',
  read_workspace_file:     '读取文件',
  write_workspace_file:    '写入文件',
  delete_workspace_file:   '删除文件',
  get_workspace_file_url:  '获取文件 URL',
  copy_workspace_file:     '复制文件',
  rename_workspace_file:   '重命名文件',
  move_workspace_file:     '移动文件',
  // H5
  list_h5_templates:       '查看 H5 模板',
  generate_h5_poster:      '生成 H5 海报',
  generate_h5_from_abc:    '从 ABC 生成 H5',
  generate_h5_from_midi:   '从 MIDI 生成 H5',
  save_h5_file:            '保存 H5 文件',
  finish_task:             '完成任务',
  // ABC
  transpose_abc:   '转调',
  change_tempo:    '调整速度',
  change_style:    '风格转换',
  add_ornament:    '添加装饰音',
  analyze_abc:     '分析谱子',
  abc_to_sky_json: '生成 Sky JSON',
  abc_to_midi_b64: '生成 MIDI',
  // 音频
  generate_audio_suno:     'Suno 生成音乐',
  generate_audio_minimax:  'MiniMax 生成音乐',
  generate_cover_minimax:  'MiniMax 翻唱',
  generate_lyrics_minimax: '生成歌词',
  abc_to_audio_prompt:     '提取音频 Prompt',
  evolve_audio_prompt:     '进化音频 Prompt',
  diff_audio_params:       '对比生成参数',
  // 音色（MiniMax）
  upload_voice_sample:       '上传音色样本',
  clone_voice_minimax:       '克隆音色',
  list_cloned_voices:        '查询已克隆音色',
  synthesize_speech_minimax: '合成语音',
  // GPT-SoVITS
  voice_clone_router:        '音色克隆路由',
  sovits_health_check:       '检查 SoVITS 服务',
  sovits_tts_and_save:       'SoVITS 语音合成',
  sovits_clone_and_save:     'SoVITS 音色克隆',
  sovits_list_models:        '查看可用模型',
  sovits_set_model:          '切换音色模型',
  sovits_list_audio_files:   '查看已保存音频',
  upload_prompt_audio:       '上传提示音频',
}

function getToolIcon(name: string): ToolIconConfig {
  return TOOL_ICON_MAP[name] ?? { bg: '#F9FAFB', fg: '#6B7280', emoji: '🔧' }
}

function getToolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, ' ')
}

// ─── JSON 语法高亮（轻量级，无依赖）─────────────────────────────────────────

function JsonHighlight({ raw }: { raw: string }) {
  if (!raw || raw === '{}' || raw === 'NULL') {
    return <span className="text-zinc-500 italic">NULL</span>
  }
  let formatted = raw
  try { formatted = JSON.stringify(JSON.parse(raw), null, 2) } catch { /* keep raw */ }

  // 简单正则高亮：key / string / number / bool / null
  const highlighted = formatted.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
    (match) => {
      if (/^"/.test(match)) {
        if (/:$/.test(match)) return `<span class="text-sky-300">${match}</span>`
        return `<span class="text-amber-300">${match}</span>`
      }
      if (/true|false/.test(match)) return `<span class="text-purple-300">${match}</span>`
      if (/null/.test(match)) return `<span class="text-red-300">${match}</span>`
      return `<span class="text-emerald-300">${match}</span>`
    }
  )
  return (
    <span
      className="text-zinc-100"
      dangerouslySetInnerHTML={{ __html: highlighted }}
    />
  )
}

// ─── 执行计时器 ───────────────────────────────────────────────────────────────

function useElapsedTimer(running: boolean) {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number | null>(null)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (running) {
      startRef.current = Date.now()
      const tick = () => {
        setElapsed(Math.floor((Date.now() - (startRef.current ?? Date.now())) / 100) * 100)
        rafRef.current = requestAnimationFrame(tick)
      }
      rafRef.current = requestAnimationFrame(tick)
    } else {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [running])

  return elapsed
}

function fmtElapsed(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
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
  const [open, setOpen] = useState(status === 'running' || status === 'failed')
  const bodyRef = useRef<HTMLDivElement>(null)
  const icon = getToolIcon(toolName)
  const label = getToolLabel(toolName)
  const isRunning = status === 'running'
  const isFailed = status === 'failed'
  const elapsed = useElapsedTimer(isRunning)

  const displayResult = error
    ? `错误: ${error}`
    : (resultText && resultText !== '{}' ? resultText : '')

  // 流式时自动展开，完成后自动折叠（用户点击后不再自动）
  const userClickedRef = useRef(false)
  useEffect(() => {
    if (!userClickedRef.current) setOpen(isRunning)
  }, [isRunning])

  // 滚到底
  useEffect(() => {
    if (open && isRunning && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [open, isRunning, argumentsJson])

  return (
    // 始终 w-full，避免展开/收起时宽度跳变闪烁
    <div
      className={[
        'w-full rounded-xl border text-xs overflow-hidden',
        isRunning
          ? 'border-orange-200 shadow-md shadow-orange-100/60'
          : isFailed
            ? 'border-red-200 shadow-sm shadow-red-50'
            : 'border-gray-100 shadow-sm',
      ].join(' ')}
    >
      {/* 头部：始终 w-full，展开按钮固定右侧 */}
      <button
        className="flex items-center gap-2 px-2.5 py-2 bg-white w-full text-left hover:bg-gray-50/80 transition-colors"
        onClick={() => { userClickedRef.current = true; setOpen((o) => !o) }}
      >
        {/* 图标徽章 */}
        <span
          className="shrink-0 w-6 h-6 rounded-lg flex items-center justify-center text-sm"
          style={{ background: icon.bg }}
        >
          {icon.emoji}
        </span>

        {/* 标签 + 工具名：min-w-0 + truncate 防止撑宽 */}
        <span className="font-semibold shrink-0" style={{ color: icon.fg }}>{label}</span>
        <span className="text-gray-300 truncate min-w-0 font-mono text-[10px]">{toolName}</span>

        {/* 状态区：ml-auto 固定右侧，shrink-0 不压缩 */}
        <span className="shrink-0 ml-auto flex items-center gap-1.5">
          {isRunning && (
            <>
              <span className="text-orange-400 font-mono text-[10px] tabular-nums">
                {fmtElapsed(elapsed)}
              </span>
              <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
            </>
          )}
          {!isRunning && !isFailed && (
            <span className="text-green-500 font-bold text-sm">✓</span>
          )}
          {isFailed && (
            <span className="text-red-500 font-bold text-sm">✗</span>
          )}
          {/* 折叠箭头：始终可见，不随展开状态改变位置 */}
          <svg
            className={['w-3 h-3 text-gray-300 transition-transform duration-200', open ? 'rotate-90' : ''].join(' ')}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </span>
      </button>

      {/* 展开体 */}
      {open && (
        <div ref={bodyRef} className="px-2.5 pb-2.5 space-y-2 bg-gray-50/60">

          {/* 参数 */}
          {argumentsJson && argumentsJson !== '{}' && (
            <div className="space-y-1 pt-2">
              <span className="text-gray-400 text-[10px] uppercase tracking-wider font-medium">
                输入参数
              </span>
              <pre className="bg-zinc-900 rounded-lg p-2.5 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words font-mono max-h-36 overflow-y-auto">
                <JsonHighlight raw={argumentsJson} />
                {isRunning && (
                  <span className="inline-block w-px h-3 bg-zinc-300 ml-0.5 animate-pulse align-text-bottom" />
                )}
              </pre>
            </div>
          )}

          {/* 结果（完成后显示） */}
          {!isRunning && displayResult && (
            <div className="space-y-1">
              <span className={[
                'text-[10px] uppercase tracking-wider font-medium',
                isFailed ? 'text-red-400' : 'text-gray-400',
              ].join(' ')}>
                {isFailed ? '错误详情' : '执行结果'}
              </span>
              <pre className={[
                'rounded-lg p-2.5 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words font-mono max-h-96 overflow-y-auto',
                isFailed ? 'bg-red-950 text-red-200' : 'bg-zinc-900 text-zinc-200',
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
