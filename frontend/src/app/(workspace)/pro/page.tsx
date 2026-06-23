'use client'

import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { useScoreStore } from '@/entities/session/store'
import { useChatStore } from '@/features/chat/store/chat.store'
import { createSession, subscribeToSession, exportScore } from '@/shared/lib/api'
import { ABCRenderer } from '@/widgets/abc-editor/ABCRenderer'
import { PipelineStatus } from '@/widgets/pipeline-status/PipelineStatus'
import { ExportPanel } from '@/widgets/export-panel/ExportPanel'
import { AudioPanel } from '@/widgets/audio-panel/AudioPanel'
import { ChatPanel } from '@/widgets/chat-panel/ChatPanel'
import Link from 'next/link'

// ─── 工作区文件树（学习 coding 的 FileExplorer，纯文件列表）─────────────────

interface WorkspaceFile {
  id: string
  icon: string
  name: string
  tag: string
  ext: string
  description: string
  available: boolean
}

function buildWorkspaceFiles(score: { meta: { title: string; key: string; bpm: number; note_count: number } } | null): WorkspaceFile[] {
  const title = score?.meta.title || 'score'
  const safe = title.replace(/[/\\:*?"<>|]/g, '_')
  return [
    {
      id: 'abc',
      icon: '🎼',
      name: `${safe}.abc`,
      tag: 'ABC',
      ext: 'abc',
      description: 'ABC 格式乐谱',
      available: !!score,
    },
    {
      id: 'json',
      icon: '🎮',
      name: `${safe}.json`,
      tag: 'JSON',
      ext: 'json',
      description: 'Sky 游戏格式',
      available: !!score,
    },
    {
      id: 'midi',
      icon: '🎹',
      name: `${safe}.mid`,
      tag: 'MIDI',
      ext: 'midi',
      description: 'MIDI 标准格式',
      available: !!score,
    },
  ]
}

type PreviewFile = WorkspaceFile | null

function WorkspaceExplorer({
  score,
  sessionId,
  onPreview,
  activeFileId,
}: {
  score: { meta: { title: string; key: string; bpm: number; note_count: number } } | null
  sessionId: string | null
  onPreview: (file: WorkspaceFile) => void
  activeFileId: string | null
}) {
  const [expanded, setExpanded] = useState(true)
  const [downloading, setDownloading] = useState<string | null>(null)
  const files = buildWorkspaceFiles(score)

  const handleDownload = useCallback(async (file: WorkspaceFile, e: ReactMouseEvent) => {
    e.stopPropagation()
    if (!sessionId || !file.available) return
    setDownloading(file.id)
    try {
      const fmt = file.ext === 'midi' ? 'midi' : file.ext as 'abc' | 'json' | 'midi'
      const blob = await exportScore(sessionId, fmt)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.name
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('下载失败', err)
    } finally {
      setDownloading(null)
    }
  }, [sessionId])

  return (
    <div className="text-xs select-none">
      {/* 工作区标题行 */}
      <div className="px-3 pt-2.5 pb-1 flex items-center justify-between">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">工作区</span>
        {score && (
          <span className="text-[9px] text-gray-300 font-mono">
            {files.filter(f => f.available).length} 个文件
          </span>
        )}
      </div>

      {/* 根目录折叠行 */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-1.5 hover:bg-gray-50 text-gray-600 font-medium transition-colors"
      >
        <svg
          className={['w-3 h-3 transition-transform text-gray-400 shrink-0', expanded ? 'rotate-90' : ''].join(' ')}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="truncate">
          📁 {score?.meta.title || '未命名谱子'}
        </span>
      </button>

      {/* 文件列表 */}
      {expanded && (
        <div className="pl-3 space-y-0.5 pb-2">
          {files.map((f) => (
            <div
              key={f.id}
              onClick={() => f.available && onPreview(f)}
              className={[
                'flex items-center gap-2 px-3 py-1.5 rounded-md transition-colors group',
                f.available
                  ? activeFileId === f.id
                    ? 'bg-orange-50 text-orange-600 cursor-pointer'
                    : 'hover:bg-gray-50 cursor-pointer text-gray-500 hover:text-gray-700'
                  : 'opacity-30 cursor-not-allowed text-gray-400',
              ].join(' ')}
            >
              <span className="shrink-0">{f.icon}</span>
              <span className="truncate flex-1 text-[11px]">{f.name}</span>

              {/* 标签 */}
              <span className={[
                'shrink-0 text-[9px] px-1 py-0.5 rounded font-mono',
                activeFileId === f.id
                  ? 'bg-orange-100 text-orange-500'
                  : 'bg-gray-100 text-gray-400 opacity-0 group-hover:opacity-100',
              ].join(' ')}>
                {f.tag}
              </span>

              {/* 下载按钮 */}
              {f.available && (
                <button
                  onClick={(e) => handleDownload(f, e)}
                  className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-orange-500"
                  title={`下载 ${f.name}`}
                >
                  {downloading === f.id ? (
                    <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                        d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                  )}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 无谱子时的空状态 */}
      {!score && (
        <div className="px-4 py-5 flex flex-col items-center gap-2 text-center">
          <span className="text-2xl opacity-20">📁</span>
          <p className="text-[10px] text-gray-300 leading-relaxed">
            在对话框粘贴 Sky JSON<br />或发送消息开始
          </p>
        </div>
      )}

      {/* 谱子元信息 */}
      {score && (
        <div className="mx-3 mt-1 px-2.5 py-2 bg-gray-50 rounded-xl space-y-1.5 border border-gray-100">
          {[
            { label: '调号', value: score.meta.key },
            { label: 'BPM',  value: String(Math.round(score.meta.bpm)) },
            { label: '音符', value: String(score.meta.note_count) },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between items-center">
              <span className="text-gray-400">{label}</span>
              <span className="text-gray-700 font-medium font-mono">{value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 中央 Tab ─────────────────────────────────────────────────────────────────

type CenterTab = 'preview' | 'logs' | 'export'

const CENTER_TABS: { id: CenterTab; label: string; icon: string }[] = [
  { id: 'preview', label: '乐谱预览', icon: '🎼' },
  { id: 'logs',    label: '执行日志', icon: '📋' },
  { id: 'export',  label: '导出',     icon: '💾' },
]

// ─── 可拖拽分隔条 ─────────────────────────────────────────────────────────────

function ResizeDivider({ onDrag }: { onDrag: (dx: number) => void }) {
  const dragging = useRef(false)
  const lastX    = useRef(0)

  const onMouseDown = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    dragging.current = true
    lastX.current = e.clientX
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      onDrag(ev.clientX - lastX.current)
      lastX.current = ev.clientX
    }
    const onUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [onDrag])

  return (
    <div
      onMouseDown={onMouseDown}
      className="w-1 shrink-0 cursor-col-resize hover:bg-orange-200 active:bg-orange-300 transition-colors bg-gray-100 relative group"
    >
      <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 flex flex-col items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {[0, 1, 2].map(i => (
          <span key={i} className="w-0.5 h-0.5 rounded-full bg-orange-400" />
        ))}
      </div>
    </div>
  )
}

// ─── 文件内容预览 ─────────────────────────────────────────────────────────────

function FileContentPreview({
  fileId,
  abcNotation,
  score,
}: {
  fileId: string | null
  abcNotation: string | null
  score: { meta: { title: string } } | null
}) {
  if (!fileId || !abcNotation) return null

  if (fileId === 'abc') {
    return (
      <div className="p-5">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs font-semibold text-gray-500">🎼 ABC 乐谱</span>
          <span className="text-[10px] text-gray-300">点击乐谱预览 Tab 查看渲染效果</span>
        </div>
        <pre className="p-4 bg-gray-50 rounded-2xl text-xs text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 leading-relaxed max-h-[calc(100vh-200px)] overflow-y-auto">
          {abcNotation}
        </pre>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-8 space-y-3">
      <span className="text-3xl opacity-30">
        {fileId === 'json' ? '🎮' : '🎹'}
      </span>
      <p className="text-sm text-gray-500">
        {fileId === 'json' ? 'Sky JSON' : 'MIDI'} 文件可在导出面板下载
      </p>
      <p className="text-xs text-gray-300">切换到「导出」Tab 下载文件</p>
    </div>
  )
}

// ─── Pro 模式页面 ─────────────────────────────────────────────────────────────

const CHAT_MIN_W = 280
const CHAT_MAX_W = 640
const CHAT_DEFAULT_W = 380

export default function ProPage() {
  const { sessionId, setSessionId, abcNotation, score, handleSSEEvent } = useScoreStore()
  const chatHandleSSE = useChatStore((s) => s.handleSSEEvent)

  const [centerTab, setCenterTab]     = useState<CenterTab>('preview')
  const [chatWidth, setChatWidth]     = useState(CHAT_DEFAULT_W)
  const [activeFileId, setActiveFileId] = useState<string | null>(null)

  const unsubRef = useRef<(() => void) | null>(null)

  // ── Session 创建 + SSE 双分发 ──────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) {
      createSession()
        .then(({ session_id }) => setSessionId(session_id))
        .catch(console.error)
      return
    }
    unsubRef.current?.()
    unsubRef.current = subscribeToSession(sessionId, (event) => {
      handleSSEEvent(event)    // → scoreStore：pipeline logs + abc 更新
      chatHandleSSE(event)     // → chatStore：流式气泡 + 工具卡片
    })
    return () => { unsubRef.current?.(); unsubRef.current = null }
  }, [sessionId, setSessionId, handleSSEEvent, chatHandleSSE])

  // 谱子更新时自动切换到预览 Tab
  useEffect(() => {
    if (abcNotation) {
      setCenterTab('preview')
      setActiveFileId(null)
    }
  }, [abcNotation])

  // ── 拖拽调整对话区宽度 ─────────────────────────────────────────────────────
  const handleResizeDrag = useCallback((dx: number) => {
    setChatWidth((w) => Math.max(CHAT_MIN_W, Math.min(CHAT_MAX_W, w - dx)))
  }, [])

  // ── 文件点击预览 ───────────────────────────────────────────────────────────
  const handleFilePreview = useCallback((file: WorkspaceFile) => {
    setActiveFileId(file.id)
    setCenterTab('preview')
  }, [])

  // 判断中央区显示内容
  const showFilePreview = activeFileId !== null && activeFileId !== 'abc' ? false : activeFileId === 'abc'

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans overflow-hidden">

      {/* ── 顶栏 ── */}
      <header className="h-10 bg-white border-b border-gray-100 flex items-center px-4 gap-3 shrink-0 shadow-sm shadow-gray-50">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 bg-orange-500 rounded-md flex items-center justify-center shadow-sm shadow-orange-200">
            <span className="text-[10px]">🎵</span>
          </div>
          <span className="font-semibold text-gray-800 text-sm">EP-Agent</span>
        </div>

        <span className="text-gray-200 text-xs">│</span>
        <span className="text-xs text-gray-400 font-medium">专业模式</span>

        {score && (
          <>
            <span className="text-gray-200 text-xs">│</span>
            <span className="text-xs text-orange-500 font-medium truncate max-w-[180px]">
              {score.meta.title}
            </span>
            <span className="text-xs text-gray-300 hidden sm:inline">
              {score.meta.key} · ♩={Math.round(score.meta.bpm)}
            </span>
          </>
        )}

        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className={['w-1.5 h-1.5 rounded-full', sessionId ? 'bg-green-400' : 'bg-gray-300'].join(' ')} />
            <span className="text-[10px] text-gray-400 font-mono hidden sm:inline">
              {sessionId ? sessionId.slice(0, 8) + '…' : '未连接'}
            </span>
          </div>
          <Link
            href="/"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-orange-50"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            返回
          </Link>
        </div>
      </header>

      {/* ── 主体三栏 ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧：工作区文件树（纯文件列表，不上传）── */}
        <aside className="w-52 bg-white border-r border-gray-100 flex flex-col overflow-hidden shrink-0">
          <div className="flex-1 overflow-y-auto">
            <WorkspaceExplorer
              score={score}
              sessionId={sessionId}
              onPreview={handleFilePreview}
              activeFileId={activeFileId}
            />
          </div>

          {/* 底部提示 */}
          <div className="border-t border-gray-50 px-3 py-2.5 shrink-0">
            <p className="text-[10px] text-gray-300 leading-relaxed text-center">
              在右侧对话框粘贴 Sky JSON<br />即可自动加载谱子
            </p>
          </div>
        </aside>

        {/* ── 中央：Tab 预览区 ── */}
        <main className="flex-1 flex flex-col overflow-hidden bg-white border-r border-gray-100 min-w-0">
          {/* Tab 栏 */}
          <div className="flex items-center border-b border-gray-100 shrink-0 bg-gray-50/50">
            {CENTER_TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => { setCenterTab(tab.id); setActiveFileId(null) }}
                className={[
                  'flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-all border-b-2 -mb-px',
                  centerTab === tab.id && activeFileId === null
                    ? 'border-orange-500 text-orange-600 bg-white'
                    : 'border-transparent text-gray-400 hover:text-gray-600 hover:bg-white/60',
                ].join(' ')}
              >
                <span>{tab.icon}</span>
                <span>{tab.label}</span>
              </button>
            ))}

            {/* 文件预览标签（点击文件树时显示）*/}
            {activeFileId && (
              <div className="flex items-center gap-1 px-3 py-2.5 border-b-2 border-orange-500 bg-white text-orange-600 text-xs font-medium ml-1">
                <span>{buildWorkspaceFiles(score).find(f => f.id === activeFileId)?.icon}</span>
                <span>{buildWorkspaceFiles(score).find(f => f.id === activeFileId)?.name}</span>
                <button
                  onClick={() => setActiveFileId(null)}
                  className="ml-1 text-orange-300 hover:text-orange-600"
                >✕</button>
              </div>
            )}
          </div>

          {/* Tab 内容 */}
          <div className="flex-1 overflow-y-auto">

            {/* 文件内容预览（优先级最高）*/}
            {activeFileId === 'abc' && (
              <FileContentPreview
                fileId={activeFileId}
                abcNotation={abcNotation}
                score={score}
              />
            )}
            {activeFileId && activeFileId !== 'abc' && (
              <FileContentPreview
                fileId={activeFileId}
                abcNotation={abcNotation}
                score={score}
              />
            )}

            {/* 正常 Tab 内容 */}
            {!activeFileId && centerTab === 'preview' && (
              abcNotation ? (
                <div className="p-5">
                  <details className="mb-4 group">
                    <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none flex items-center gap-1.5 list-none">
                      <svg
                        className="w-2.5 h-2.5 transition-transform group-open:rotate-90 text-gray-300"
                        fill="none" stroke="currentColor" viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      <span>ABC 源码</span>
                    </summary>
                    <pre className="mt-2 p-3 bg-gray-50 rounded-xl text-xs text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 max-h-40 overflow-y-auto">
                      {abcNotation}
                    </pre>
                  </details>
                  <div className="border border-gray-100 rounded-2xl overflow-hidden shadow-sm">
                    <ABCRenderer abc={abcNotation} title={score?.meta.title} />
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center p-10 space-y-4">
                  <div className="w-16 h-16 bg-gradient-to-br from-gray-50 to-gray-100 rounded-2xl flex items-center justify-center text-4xl shadow-inner">
                    🎼
                  </div>
                  <div className="space-y-1.5">
                    <p className="text-sm font-semibold text-gray-600">在右侧对话框开始</p>
                    <p className="text-xs text-gray-400 max-w-xs leading-relaxed">
                      粘贴 Sky JSON 谱子，或直接告诉 AI 你想做什么
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 text-xs text-gray-400 max-w-xs">
                    {[
                      ['💬', '直接说话', '「生成中国风配乐」「升高八度」'],
                      ['📋', '粘贴 JSON', '自动识别 Sky 谱子并转换'],
                      ['🎵', '附加音频', '粘贴 MP3 克隆你的声音'],
                    ].map(([icon, title, desc]) => (
                      <div key={title} className="flex items-start gap-2 px-3 py-2 bg-gray-50 rounded-xl border border-gray-100 text-left">
                        <span className="text-base shrink-0">{icon}</span>
                        <div>
                          <p className="font-medium text-gray-600">{title}</p>
                          <p className="text-gray-400 text-[11px]">{desc}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}

            {!activeFileId && centerTab === 'logs' && (
              <div className="p-4">
                <PipelineStatus />
              </div>
            )}

            {!activeFileId && centerTab === 'export' && <ExportPanel />}
          </div>
        </main>

        {/* ── 可拖拽分隔条 ── */}
        <ResizeDivider onDrag={handleResizeDrag} />

        {/* ── 右侧：对话面板 ── */}
        <aside
          style={{ width: chatWidth }}
          className="flex flex-col overflow-hidden shrink-0 bg-white"
        >
          {/* 音频生成折叠区 */}
          <details className="border-b border-gray-100 group shrink-0">
            <summary className="px-3 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider cursor-pointer hover:text-gray-600 flex items-center gap-1.5 select-none list-none transition-colors hover:bg-gray-50">
              <svg
                className="w-3 h-3 transition-transform group-open:rotate-90 text-gray-300 shrink-0"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              🎵 音频生成
              <span className="ml-auto text-[9px] text-gray-300 font-normal normal-case tracking-normal">
                点击展开
              </span>
            </summary>
            <div className="max-h-72 overflow-y-auto border-t border-gray-50">
              <AudioPanel />
            </div>
          </details>

          {/* 主对话面板 */}
          <div className="flex-1 overflow-hidden">
            <ChatPanel />
          </div>
        </aside>

      </div>
    </div>
  )
}


