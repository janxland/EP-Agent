'use client'

import { useState, useEffect, useRef, memo, useCallback } from 'react'
import type { MouseEvent } from 'react'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { useScoreStore } from '@/entities/session/store'
import { ABCRenderer } from '@/widgets/abc-editor/ABCRenderer'

// ─── 工具配置（图标 + 标签合表，单一数据源）─────────────────────────────────

interface ToolConfig { bg: string; fg: string; emoji: string; label: string }

const TOOL_CONFIG: Record<string, ToolConfig> = {
  intent_router:      { bg: '#FFF7ED', fg: '#EA580C', emoji: '🧭', label: '意图识别' },
  convert_sky_json:   { bg: '#ECFEFF', fg: '#0891B2', emoji: '🎮', label: '解析 Sky 谱子' },
  abc_editor:         { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️', label: 'ABC 谱子编辑' },
  abc_composer:       { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🎵', label: 'ABC 谱子创作' },
  audio_generator:    { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎧', label: '音频生成' },
  voice_clone:        { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🎤', label: '音色克隆' },
  h5_generator:       { bg: '#FFF1F2', fg: '#E11D48', emoji: '🎨', label: 'H5 海报生成' },
  list_workspace_files:    { bg: '#F0FDF4', fg: '#16A34A', emoji: '📂', label: '列出工作区文件' },
  read_workspace_file:     { bg: '#EFF6FF', fg: '#2563EB', emoji: '📄', label: '读取文件' },
  write_workspace_file:    { bg: '#FFF7ED', fg: '#EA580C', emoji: '💾', label: '写入文件' },
  delete_workspace_file:   { bg: '#FFF1F2', fg: '#E11D48', emoji: '🗑️', label: '删除文件' },
  get_workspace_file_url:  { bg: '#F0FDF4', fg: '#16A34A', emoji: '🔗', label: '获取文件 URL' },
  copy_workspace_file:     { bg: '#ECFEFF', fg: '#0891B2', emoji: '📋', label: '复制文件' },
  rename_workspace_file:   { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️', label: '重命名文件' },
  move_workspace_file:     { bg: '#EDE9FE', fg: '#7C3AED', emoji: '📦', label: '移动文件' },
  list_h5_templates:       { bg: '#FFF1F2', fg: '#E11D48', emoji: '🖼️', label: '查看 H5 模板' },
  generate_h5_poster:      { bg: '#FFF1F2', fg: '#E11D48', emoji: '🎨', label: '生成 H5 海报' },
  generate_h5_from_abc:    { bg: '#FFF1F2', fg: '#BE123C', emoji: '🎵', label: '从 ABC 生成 H5' },
  generate_h5_from_midi:   { bg: '#FFF1F2', fg: '#BE123C', emoji: '🎹', label: '从 MIDI 生成 H5' },
  save_h5_file:            { bg: '#F0FDF4', fg: '#15803D', emoji: '💾', label: '保存 H5 文件' },
  finish_task:             { bg: '#F0FDF4', fg: '#15803D', emoji: '✅', label: '完成任务' },
  transpose_abc:      { bg: '#FFFBEB', fg: '#D97706', emoji: '🎵', label: '转调' },
  change_tempo:       { bg: '#FFFBEB', fg: '#B45309', emoji: '🥁', label: '调整速度' },
  change_style:       { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🎨', label: '风格转换' },
  add_ornament:       { bg: '#F0FDF4', fg: '#15803D', emoji: '🎶', label: '添加装饰音' },
  analyze_abc:        { bg: '#EFF6FF', fg: '#1D4ED8', emoji: '🔬', label: '分析谱子' },
  abc_to_sky_json:    { bg: '#ECFEFF', fg: '#0E7490', emoji: '🎮', label: '生成 Sky JSON' },
  abc_to_midi_b64:    { bg: '#EEF2FF', fg: '#4338CA', emoji: '🎹', label: '生成 MIDI' },
  generate_audio_suno:     { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎸', label: 'Suno 生成音乐' },
  generate_audio_minimax:  { bg: '#EFF6FF', fg: '#2563EB', emoji: '🎼', label: 'MiniMax 生成音乐' },
  generate_cover_minimax:  { bg: '#FDF2F8', fg: '#C026D3', emoji: '🎤', label: 'MiniMax 翻唱' },
  generate_lyrics_minimax: { bg: '#FFF7ED', fg: '#EA580C', emoji: '📝', label: '生成歌词' },
  abc_to_audio_prompt:     { bg: '#F0FDFA', fg: '#0D9488', emoji: '🔊', label: '提取音频 Prompt' },
  evolve_audio_prompt:     { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🔄', label: '进化音频 Prompt' },
  diff_audio_params:       { bg: '#F9FAFB', fg: '#374151', emoji: '📊', label: '对比生成参数' },
  upload_voice_sample:       { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎙️', label: '上传音色样本' },
  clone_voice_minimax:       { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🧬', label: '克隆音色' },
  list_cloned_voices:        { bg: '#EDE9FE', fg: '#5B21B6', emoji: '📋', label: '查询已克隆音色' },
  synthesize_speech_minimax: { bg: '#EDE9FE', fg: '#4C1D95', emoji: '🔈', label: '合成语音' },
  voice_clone_router:        { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🎙️', label: '音色克隆路由' },
  sovits_health_check:       { bg: '#F0FDF4', fg: '#16A34A', emoji: '💚', label: '检查 SoVITS 服务' },
  sovits_tts_and_save:       { bg: '#EDE9FE', fg: '#7C3AED', emoji: '🔊', label: 'SoVITS 语音合成' },
  sovits_clone_and_save:     { bg: '#EDE9FE', fg: '#6D28D9', emoji: '🧬', label: 'SoVITS 音色克隆' },
  sovits_list_models:        { bg: '#EFF6FF', fg: '#2563EB', emoji: '📋', label: '查看可用模型' },
  sovits_set_model:          { bg: '#FFFBEB', fg: '#D97706', emoji: '🔄', label: '切换音色模型' },
  sovits_list_audio_files:   { bg: '#F0FDF4', fg: '#15803D', emoji: '🎵', label: '查看已保存音频' },
  upload_prompt_audio:       { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎤', label: '上传提示音频' },

  // ── abc_edit 组 ────────────────────────────────────────────────────────────
  abc_to_midi:               { bg: '#EEF2FF', fg: '#4338CA', emoji: '🎹', label: '生成 MIDI 文件' },
  validate_abc:              { bg: '#F0FDF4', fg: '#16A34A', emoji: '✔️',  label: '验证 ABC 音域' },

  // ── agent_call 组（SupervisorAgent 调用子 Agent）──────────────────────────
  call_audio_agent:          { bg: '#FAF5FF', fg: '#9333EA', emoji: '🎧', label: '调用音频 Agent' },
  call_convert_agent:        { bg: '#ECFEFF', fg: '#0891B2', emoji: '🔄', label: '调用转换 Agent' },
  call_h5_agent:             { bg: '#FFF1F2', fg: '#E11D48', emoji: '🎨', label: '调用 H5 Agent' },

  // ── audio 组 ──────────────────────────────────────────────────────────────
  get_suno_job_status:       { bg: '#FAF5FF', fg: '#9333EA', emoji: '⏳', label: '查询 Suno 任务' },

  // ── h5 组 ─────────────────────────────────────────────────────────────────
  get_h5_template:           { bg: '#FFF1F2', fg: '#E11D48', emoji: '📋', label: '读取 H5 模板' },
  parse_abc_to_json:         { bg: '#FFFBEB', fg: '#D97706', emoji: '🔍', label: '解析 ABC 元数据' },
  parse_sky_json_to_json:    { bg: '#ECFEFF', fg: '#0891B2', emoji: '🎮', label: '解析 Sky JSON' },
  save_h5_output:            { bg: '#F0FDF4', fg: '#15803D', emoji: '💾', label: '保存 H5 输出' },

  // ── workspace 组 ──────────────────────────────────────────────────────────
  edit_workspace_file:       { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️',  label: '精准编辑文件' },
  read_workspace_files:      { bg: '#EFF6FF', fg: '#2563EB', emoji: '📚', label: '批量读取文件' },
  run_write_tasks_in_parallel: { bg: '#FFF7ED', fg: '#EA580C', emoji: '⚡', label: '并行写入文件' },

  // ── audio 保存组 ──────────────────────────────────────────────────────────────
  save_audio_from_url:       { bg: '#F0FDF4', fg: '#15803D', emoji: '💿', label: '保存音频到工作区' },

  // ── llm: SubAgent 内部 LLM 调用（AUDIT-FIX-02：让 ABC 创作 LLM 在 Timeline 可见）──
  // create_agent 发布 tool.call(tool='llm:create_main') 供审计 + 前端渲染
  'llm:create_main':    { bg: '#FAF5FF', fg: '#7C3AED', emoji: '✨', label: 'AI 创作 ABC' },
  'llm:quality_fix':    { bg: '#FFF7ED', fg: '#EA580C', emoji: '🔧', label: 'AI 质量修正' },
  'llm:validate':       { bg: '#F0FDF4', fg: '#16A34A', emoji: '✔️',  label: 'AI 验证 ABC' },
  'llm:edit_main':      { bg: '#FFFBEB', fg: '#D97706', emoji: '✏️',  label: 'AI 编辑 ABC' },
  'llm:convert_main':   { bg: '#ECFEFF', fg: '#0891B2', emoji: '🔄', label: 'AI 转换处理' },
  'llm:query_main':     { bg: '#EFF6FF', fg: '#2563EB', emoji: '💬', label: 'AI 问答' },
}

const _DEFAULT_CONFIG: ToolConfig = { bg: '#F9FAFB', fg: '#6B7280', emoji: '🔧', label: '' }
function getToolConfig(name: string): ToolConfig {
  if (TOOL_CONFIG[name]) return TOOL_CONFIG[name]
  // llm: 前缀通配：未精确匹配时 fallback 到 llm: 通用样式（AI 推理调用）
  if (name.startsWith('llm:')) {
    return { bg: '#FAF5FF', fg: '#7C3AED', emoji: '🤖', label: `AI · ${name.slice(4).replace(/_/g, ' ')}` }
  }
  return { ..._DEFAULT_CONFIG, label: name.replace(/_/g, ' ') }
}

// ─── 文件类型判断 ─────────────────────────────────────────────────────────────

const AUDIO_EXTS = new Set(['.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.opus'])
const VIDEO_EXTS = new Set(['.mp4', '.webm', '.mov', '.avi', '.mkv'])
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'])

type FileKind = 'audio' | 'video' | 'image' | 'other'

function getFileKind(filename: string): FileKind {
  const ext = ('.' + filename.split('.').pop()).toLowerCase()
  if (AUDIO_EXTS.has(ext)) return 'audio'
  if (VIDEO_EXTS.has(ext)) return 'video'
  if (IMAGE_EXTS.has(ext)) return 'image'
  return 'other'
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

// ─── 从结果 JSON 中提取文件信息 ───────────────────────────────────────────────

interface FileInfo {
  workspace_path: string
  filename: string
  size_bytes?: number
  kind: FileKind
  url: string  // /api/workspace/file?path=...
}

function extractFileInfo(resultText: string, wsId: string, projId: string): FileInfo | null {
  if (!resultText || !wsId) return null
  try {
    const obj = JSON.parse(resultText)
    const wp: string = obj.workspace_path || obj.output_path || ''
    if (!wp) return null
    const filename = wp.split('/').pop() || wp
    const kind = getFileKind(filename)
    if (kind === 'other') return null
    // 构造访问 URL：GET /api/workspaces/{ws_id}/files/content?path=xxx&project_id=xxx&encoding=raw
    // 注意：必须加 /api 前缀，next.config.js 的代理规则是 /api/:path* → 后端
    const params = new URLSearchParams({ path: wp, encoding: 'raw' })
    if (projId) params.set('project_id', projId)
    const url = `/api/workspaces/${encodeURIComponent(wsId)}/files/content?${params.toString()}`
    return {
      workspace_path: wp,
      filename,
      size_bytes: obj.size_bytes,
      kind,
      url,
    }
  } catch {
    return null
  }
}

// ─── 音频播放卡片 ─────────────────────────────────────────────────────────────

function AudioFileCard({ info }: { info: FileInfo }) {
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(false)
  const audioRef = useRef<HTMLAudioElement>(null)

  const fmtTime = (s: number) => {
    if (!isFinite(s)) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  const togglePlay = useCallback(() => {
    const a = audioRef.current
    if (!a) return
    if (playing) { a.pause() } else { a.play().catch(() => setError(true)) }
  }, [playing])

  const handleSeek = useCallback((e: MouseEvent<HTMLDivElement>) => {
    const a = audioRef.current
    if (!a || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    a.currentTime = ratio * duration
    setProgress(ratio * duration)
  }, [duration])

  return (
    <div className="mt-2 rounded-2xl overflow-hidden border border-violet-100 bg-gradient-to-br from-violet-50 to-purple-50 shadow-sm">
      {/* 音频元素 */}
      <audio
        ref={audioRef}
        src={info.url}
        preload="metadata"
        onLoadedMetadata={e => { setDuration((e.target as HTMLAudioElement).duration); setLoaded(true) }}
        onTimeUpdate={e => setProgress((e.target as HTMLAudioElement).currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => { setPlaying(false); setProgress(0) }}
        onError={() => setError(true)}
      />

      <div className="px-3.5 py-3 flex items-center gap-3">
        {/* 播放按钮 */}
        <button
          onClick={togglePlay}
          disabled={error}
          className={[
            'shrink-0 w-10 h-10 rounded-full flex items-center justify-center',
            'shadow-md transition-all duration-200 active:scale-95',
            error
              ? 'bg-gray-200 cursor-not-allowed'
              : playing
                ? 'bg-violet-500 hover:bg-violet-600 shadow-violet-200'
                : 'bg-white hover:bg-violet-50 shadow-violet-100 border border-violet-100',
          ].join(' ')}
        >
          {error ? (
            <span className="text-gray-400 text-xs">✗</span>
          ) : playing ? (
            /* 暂停图标 */
            <svg className="w-4 h-4 text-white" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>
            </svg>
          ) : (
            /* 播放图标 */
            <svg className="w-4 h-4 text-violet-500" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
          )}
        </button>

        {/* 中间：文件名 + 进度条 */}
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-violet-700 truncate">{info.filename}</span>
            {info.size_bytes && (
              <span className="text-[10px] text-violet-400 shrink-0">{fmtBytes(info.size_bytes)}</span>
            )}
          </div>

          {/* 进度条 */}
          <div
            className="relative h-1.5 bg-violet-100 rounded-full cursor-pointer group"
            onClick={handleSeek}
          >
            <div
              className="absolute inset-y-0 left-0 bg-violet-400 rounded-full transition-all"
              style={{ width: duration ? `${(progress / duration) * 100}%` : '0%' }}
            />
            {/* 拖拽点 */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-violet-500 rounded-full shadow opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ left: duration ? `calc(${(progress / duration) * 100}% - 6px)` : '-6px' }}
            />
          </div>

          {/* 时间 */}
          <div className="flex justify-between text-[10px] text-violet-400 tabular-nums">
            <span>{fmtTime(progress)}</span>
            <span>{loaded ? fmtTime(duration) : '--:--'}</span>
          </div>
        </div>

        {/* 波形装饰 */}
        <div className="shrink-0 flex items-end gap-0.5 h-6">
          {[3, 5, 8, 6, 9, 5, 7, 4, 8, 6].map((h, i) => (
            <div
              key={i}
              className={['w-0.5 rounded-full transition-all duration-150',
                playing ? 'bg-violet-400' : 'bg-violet-200'].join(' ')}
              style={{
                height: `${playing ? h * (0.6 + 0.4 * Math.sin(Date.now() / 200 + i)) : h * 0.5}px`,
              }}
            />
          ))}
        </div>
      </div>

      {error && (
        <div className="px-3.5 pb-2 text-[10px] text-red-400">音频加载失败，请检查文件路径</div>
      )}
    </div>
  )
}

// ─── 视频/图片文件卡片（点击在预览区打开）────────────────────────────────────

function MediaFileCard({ info }: { info: FileInfo }) {
  const handleOpen = useCallback(() => {
    window.open(info.url, '_blank')
  }, [info])

  const isVideo = info.kind === 'video'
  const isImage = info.kind === 'image'

  return (
    <div
      className="mt-2 rounded-2xl overflow-hidden border border-gray-100 bg-gray-50 shadow-sm cursor-pointer group hover:border-blue-200 hover:bg-blue-50/30 transition-all"
      onClick={handleOpen}
    >
      <div className="px-3.5 py-3 flex items-center gap-3">
        {/* 图标 */}
        <div className={[
          'shrink-0 w-10 h-10 rounded-xl flex items-center justify-center text-xl',
          isVideo ? 'bg-blue-100' : 'bg-emerald-100',
        ].join(' ')}>
          {isVideo ? '🎬' : '🖼️'}
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-semibold text-gray-700 truncate">{info.filename}</div>
          <div className="text-[10px] text-gray-400 mt-0.5 flex items-center gap-1.5">
            {info.size_bytes && <span>{fmtBytes(info.size_bytes)}</span>}
            <span className="text-gray-300">·</span>
            <span className={isVideo ? 'text-blue-400' : 'text-emerald-400'}>
              点击在预览区打开
            </span>
          </div>
        </div>

        {/* 箭头 */}
        <svg className="w-4 h-4 text-gray-300 group-hover:text-blue-400 transition-colors shrink-0"
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
        </svg>
      </div>

      {/* 图片预览缩略图 */}
      {isImage && (
        <div className="px-3.5 pb-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={info.url}
            alt={info.filename}
            className="w-full max-h-32 object-cover rounded-xl border border-gray-100"
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        </div>
      )}
    </div>
  )
}

// ─── ABC 实时预览卡片 ────────────────────────────────────────────────────────
// 嵌入在 llm:create_main / abc_composer 工具卡片底部
// 生成中：监听 scoreStore.abcNotation 实时渲染（abc.updated 每200字符推送一次）
// 完成后：显示最终完整谱子 + 播放控制

// 触发 ABC 实时预览的工具名集合
const ABC_LIVE_TOOLS = new Set(['llm:create_main', 'abc_composer'])

function AbcLiveCard({ status }: { status: 'running' | 'succeeded' | 'failed' }) {
  const abcNotation = useScoreStore((s) => s.abcNotation)
  const [expanded, setExpanded] = useState(true)

  // 没有 ABC 内容时不渲染
  if (!abcNotation) return null

  const isRunning = status === 'running'

  return (
    <div className="mt-2 rounded-xl border border-violet-100 bg-white overflow-hidden">
      {/* 卡片头部 */}
      <button
        className="flex items-center gap-2 w-full px-3 py-2 bg-gradient-to-r from-violet-50 to-purple-50 hover:from-violet-100 hover:to-purple-100 transition-colors text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-base">🎵</span>
        <span className="text-[11px] font-semibold text-violet-700">
          {isRunning ? '正在生成乐谱...' : '乐谱预览'}
        </span>
        {isRunning && (
          <span className="flex items-center gap-1 text-[10px] text-violet-400">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
            实时渲染中
          </span>
        )}
        <svg
          className={['w-3 h-3 text-violet-300 ml-auto transition-transform duration-200', expanded ? 'rotate-90' : ''].join(' ')}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
      </button>

      {/* ABCRenderer */}
      {expanded && (
        <div className="border-t border-violet-50">
          <ABCRenderer
            abc={abcNotation}
            className="min-h-0"
          />
        </div>
      )}
    </div>
  )
}

// ─── JSON 语法高亮 ────────────────────────────────────────────────────────────

function JsonHighlight({ raw }: { raw: string }) {
  if (!raw || raw === '{}' || raw === 'NULL') {
    return <span className="text-zinc-500 italic">NULL</span>
  }
  let formatted = raw
  try { formatted = JSON.stringify(JSON.parse(raw), null, 2) } catch { /* keep raw */ }

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
    <span className="text-zinc-100" dangerouslySetInnerHTML={{ __html: highlighted }} />
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

// ─── ToolCard 主组件 ──────────────────────────────────────────────────────────

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
  const { label, ...icon } = getToolConfig(toolName)
  const isRunning = status === 'running'
  const isFailed = status === 'failed'
  const elapsed = useElapsedTimer(isRunning)

  // 从 workspace store 获取当前 ws/proj id，用于构造文件 URL
  const { activeWorkspaceId, activeProjectId } = useWorkspaceStore()

  const displayResult = error
    ? `错误: ${error}`
    : (resultText && resultText !== '{}' ? resultText : '')

  // 解析文件信息（仅成功时）
  const fileInfo = (!isRunning && !isFailed && displayResult)
    ? extractFileInfo(displayResult, activeWorkspaceId || '', activeProjectId || '')
    : null

  // 流式时自动展开，完成后自动折叠（用户点击后不再自动）
  const userClickedRef = useRef(false)
  useEffect(() => {
    if (!userClickedRef.current) setOpen(isRunning || isFailed)
  }, [isRunning, isFailed])

  // 滚到底
  useEffect(() => {
    if (open && isRunning && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [open, isRunning, argumentsJson])

  return (
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
      {/* 头部 */}
      <button
        className="flex items-center gap-2 px-2.5 py-2 bg-white w-full text-left hover:bg-gray-50/80 transition-colors"
        onClick={() => { userClickedRef.current = true; setOpen((o) => !o) }}
      >
        <span
          className="shrink-0 w-6 h-6 rounded-lg flex items-center justify-center text-sm"
          style={{ background: icon.bg }}
        >
          {icon.emoji}
        </span>
        <span className="font-semibold shrink-0" style={{ color: icon.fg }}>{label}</span>
        <span className="text-gray-300 truncate min-w-0 font-mono text-[10px]">{toolName}</span>

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
              <pre className="bg-zinc-900 rounded-lg p-2.5 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words font-mono max-h-48 overflow-y-auto">
                <JsonHighlight raw={argumentsJson} />
                {isRunning && (
                  <span className="inline-block w-px h-3 bg-zinc-300 ml-0.5 animate-pulse align-text-bottom" />
                )}
              </pre>
            </div>
          )}

          {/* ABC 实时预览（create_main / abc_composer 专属） */}
          {ABC_LIVE_TOOLS.has(toolName) && (
            <AbcLiveCard status={status} />
          )}

          {/* 结果区域 */}
          {!isRunning && displayResult && (
            <div className="space-y-1">
              <span className={[
                'text-[10px] uppercase tracking-wider font-medium',
                isFailed ? 'text-red-400' : 'text-gray-400',
              ].join(' ')}>
                {isFailed ? '错误详情' : '执行结果'}
              </span>

              {/* 文件卡片（优先渲染） */}
              {fileInfo && fileInfo.kind === 'audio' && (
                <AudioFileCard info={fileInfo} />
              )}
              {fileInfo && (fileInfo.kind === 'video' || fileInfo.kind === 'image') && (
                <MediaFileCard info={fileInfo} />
              )}

              {/* 原始 JSON（文件卡片下方折叠展示，或无文件时展示） */}
              {(!fileInfo || isFailed) && (
                <pre className={[
                  'rounded-lg p-2.5 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words font-mono overflow-y-auto max-h-48',
                  isFailed ? 'bg-red-950 text-red-200' : 'bg-zinc-900 text-zinc-200',
                ].join(' ')}>
                  {displayResult}
                </pre>
              )}

              {/* 有文件卡片时，原始 JSON 可折叠查看 */}
              {fileInfo && !isFailed && (
                <RawJsonCollapse raw={displayResult} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
})

// ─── 可折叠的原始 JSON（文件卡片下方） ───────────────────────────────────────

function RawJsonCollapse({ raw }: { raw: string }) {
  const [show, setShow] = useState(false)
  return (
    <div>
      <button
        className="text-[10px] text-gray-400 hover:text-gray-600 transition-colors flex items-center gap-1 mt-1"
        onClick={() => setShow(v => !v)}
      >
        <svg className={['w-2.5 h-2.5 transition-transform', show ? 'rotate-90' : ''].join(' ')}
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        {show ? '收起原始数据' : '查看原始数据'}
      </button>
      {show && (
        <pre className="mt-1 bg-zinc-900 rounded-lg p-2.5 text-[11px] leading-relaxed overflow-x-hidden whitespace-pre-wrap break-words font-mono overflow-y-auto max-h-36">
          <JsonHighlight raw={raw} />
        </pre>
      )}
    </div>
  )
}
