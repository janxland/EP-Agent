'use client'

/**
 * /pro/[sessionId] — 动态路由专业模式页面
 *
 * 核心功能：
 *   - 从 URL 读取 sessionId，刷新后直接恢复对话状态
 *   - 调用 restoreFromSessionId() 恢复 workspaceId
 *   - SSE 双分发：scoreStore + chatStore
 *   - 与 /pro 页面共享相同 UI，仅 sessionId 来源不同
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useScoreStore } from '@/entities/session/store'
import { useChatStore } from '@/features/chat/store/chat.store'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { subscribeToSession, exportScore, getSessionMessages, getSessionTodos } from '@/shared/lib/api'
import { ABCRenderer } from '@/widgets/abc-editor/ABCRenderer'
import { PipelineStatus } from '@/widgets/pipeline-status/PipelineStatus'
import { ExportPanel } from '@/widgets/export-panel/ExportPanel'
import { AudioPanel } from '@/widgets/audio-panel/AudioPanel'
import { ChatPanel } from '@/widgets/chat-panel/ChatPanel'
import { WorkspaceSidebar } from '@/widgets/workspace-sidebar/WorkspaceSidebar'
import { RoleSwitcher } from '@/widgets/role-switcher'
import Link from 'next/link'

// ─── 工作区文件树 ─────────────────────────────────────────────────────────────

interface WorkspaceFile {
  id: string
  icon: string
  name: string
  tag: string
  ext: string
  available: boolean
}

function buildWorkspaceFiles(score: { meta: { title: string } } | null): WorkspaceFile[] {
  const title = score?.meta.title || 'score'
  const safe = title.replace(/[/\\:*?"<>|]/g, '_')
  return [
    { id: 'abc',  icon: '🎼', name: `${safe}.abc`,  tag: 'ABC',  ext: 'abc',  available: !!score },
    { id: 'json', icon: '🎮', name: `${safe}.json`, tag: 'JSON', ext: 'json', available: !!score },
    { id: 'midi', icon: '🎹', name: `${safe}.mid`,  tag: 'MIDI', ext: 'midi', available: !!score },
  ]
}

function WorkspaceExplorer({
  score, sessionId, onPreview, activeFileId,
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
      a.href = url; a.download = file.name; a.click()
      URL.revokeObjectURL(url)
    } catch (err) { console.error('下载失败', err) }
    finally { setDownloading(null) }
  }, [sessionId])

  return (
    <div className="text-xs select-none">
      <div className="px-3 pt-2.5 pb-1 flex items-center justify-between">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">谱子文件</span>
        {score && (
          <span className="text-[9px] text-gray-300 font-mono">
            {files.filter(f => f.available).length} 个文件
          </span>
        )}
      </div>
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-1.5 hover:bg-gray-50 text-gray-600 font-medium transition-colors"
      >
        <svg className={['w-3 h-3 transition-transform text-gray-400 shrink-0', expanded ? 'rotate-90' : ''].join(' ')}
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span className="truncate">📁 {score?.meta.title || '未命名谱子'}</span>
      </button>
      {expanded && (
        <div className="pl-3 space-y-0.5 pb-2">
          {files.map((f) => (
            <div key={f.id} onClick={() => f.available && onPreview(f)}
              className={['flex items-center gap-2 px-3 py-1.5 rounded-md transition-colors group',
                f.available
                  ? activeFileId === f.id ? 'bg-orange-50 text-orange-600 cursor-pointer'
                    : 'hover:bg-gray-50 cursor-pointer text-gray-500 hover:text-gray-700'
                  : 'opacity-30 cursor-not-allowed text-gray-400',
              ].join(' ')}
            >
              <span className="shrink-0">{f.icon}</span>
              <span className="truncate flex-1 text-[11px]">{f.name}</span>
              <span className={['shrink-0 text-[9px] px-1 py-0.5 rounded font-mono',
                activeFileId === f.id ? 'bg-orange-100 text-orange-500'
                  : 'bg-gray-100 text-gray-400 opacity-0 group-hover:opacity-100',
              ].join(' ')}>{f.tag}</span>
              {f.available && (
                <button onClick={(e) => handleDownload(f, e)}
                  className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-orange-500"
                  title={`下载 ${f.name}`}>
                  {downloading === f.id
                    ? <svg className="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    : <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                          d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                      </svg>
                  }
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {!score && (
        <div className="px-4 py-5 flex flex-col items-center gap-2 text-center">
          <span className="text-2xl opacity-20">📁</span>
          <p className="text-[10px] text-gray-300 leading-relaxed">在对话框粘贴 Sky JSON<br />或发送消息开始</p>
        </div>
      )}
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

// ─── 可拖拽分隔条 ─────────────────────────────────────────────────────────────

function ResizeDivider({ onDrag }: { onDrag: (dx: number) => void }) {
  const dragging = useRef(false)
  const lastX = useRef(0)
  const onMouseDown = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    dragging.current = true; lastX.current = e.clientX
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const onMove = (ev: MouseEvent) => { if (!dragging.current) return; onDrag(ev.clientX - lastX.current); lastX.current = ev.clientX }
    const onUp = () => { dragging.current = false; document.body.style.cursor = ''; document.body.style.userSelect = ''; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [onDrag])
  return (
    <div onMouseDown={onMouseDown}
      className="w-1 shrink-0 cursor-col-resize hover:bg-orange-200 active:bg-orange-300 transition-colors bg-gray-100 relative group">
      <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 flex flex-col items-center justify-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {[0, 1, 2].map(i => <span key={i} className="w-0.5 h-0.5 rounded-full bg-orange-400" />)}
      </div>
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

// ─── 主页面 ───────────────────────────────────────────────────────────────────

const CHAT_MIN_W = 280
const CHAT_MAX_W = 640
const CHAT_DEFAULT_W = 380

export default function ProSessionPage() {
  const params = useParams()
  const router = useRouter()
  const sessionId = params.sessionId as string

  const { setSessionId, abcNotation, score, handleSSEEvent, reset: resetScore } = useScoreStore()
  const { handleSSEEvent: chatHandleSSE, setMessages, setTodos, resetRuntime, restoreRoleFromSession,
          activeRoleId, activeRoleName, activeRoleIcon } = useChatStore(
    (s) => ({
      handleSSEEvent: s.handleSSEEvent,
      setMessages: s.setMessages,
      setTodos: s.setTodos,
      resetRuntime: s.resetRuntime,
      restoreRoleFromSession: s.restoreRoleFromSession,
      activeRoleId:   s.activeRoleId,
      activeRoleName: s.activeRoleName,
      activeRoleIcon: s.activeRoleIcon,
    })
  )
  const { restoreFromSessionId, setActiveSessionId, _pendingNavigateSessionId, clearPendingNavigate, activeSessions } = useWorkspaceStore()

  // 用 useMemo 缓存 activeSessions 结果，避免每次渲染都重新遍历 workspaces
  // activeSessions() 依赖 workspaces，workspaces 变化时自动重新计算
  const { workspaces } = useWorkspaceStore()
  const activeSessionsList = useMemo(() => activeSessions(), [activeSessions, workspaces])

  // 从 workspace store 读取当前 session 的标题（比 sessionId 截断更友好）
  const sessionTitle = activeSessionsList.find((s) => s.id === sessionId)?.title ?? null

  const [centerTab, setCenterTab]       = useState<CenterTab>('preview')
  const [chatWidth, setChatWidth]       = useState(CHAT_DEFAULT_W)
  const [activeFileId, setActiveFileId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen]   = useState(true)
  const [showRoleSwitcher, setShowRoleSwitcher] = useState(false)
  // 历史恢复状态（防止重复加载）
  const historyLoadedRef = useRef<string | null>(null)

  const unsubRef = useRef<(() => void) | null>(null)

  // ── 监听删除后的跳转信号 ─────────────────────────────────────────────────────
  // 用 useRef 存 router 避免 router 对象变化引发多余 re-run
  const routerRef = useRef(router)
  useEffect(() => { routerRef.current = router })

  useEffect(() => {
    if (_pendingNavigateSessionId === undefined) return
    clearPendingNavigate()
    if (_pendingNavigateSessionId) {
      routerRef.current.replace(`/pro/${_pendingNavigateSessionId}`)
    } else {
      routerRef.current.replace('/pro')
    }
  }, [_pendingNavigateSessionId, clearPendingNavigate])

  // ── 初始化：从 URL sessionId 恢复状态 ────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) return
    // ⚠️ 必须先断开旧 SSE 连接，再清空状态
    // 否则旧连接的 replay 事件会在清空后继续写入，污染新 session
    unsubRef.current?.()
    unsubRef.current = null
    // 清空旧的对话状态 + 谱子状态
    resetScore()
    resetRuntime()
    setMessages([])
    setTodos([], '')
    // 重置历史加载标记，确保 session 切换后新 session 历史一定被加载
    historyLoadedRef.current = null
    setSessionId(sessionId)
    setActiveSessionId(sessionId)
    restoreFromSessionId(sessionId)
    // 从服务端恢复角色状态（切换 session 后角色可能不同）
    restoreRoleFromSession(sessionId)
  }, [sessionId, setSessionId, setActiveSessionId, restoreFromSessionId, resetScore, resetRuntime, setMessages, setTodos, restoreRoleFromSession])

  // ── 历史消息恢复（参考 magic-coding 的 backlog 机制）────────────────────────
  // 刷新后从后端拉取已落库的历史消息和 TODO，直接注入 store。
  // 与 SSE replay 的关系：
  //   - HTTP 拉取：更快，session 切换后立即恢复历史
  //   - SSE message.history：store 中已有 msg_id 时自动去重跳过（见 chat.store.ts）
  //   两路写入不会重复，因为 SSE replay 的 message.history 事件有 msg_id 去重保护
  useEffect(() => {
    if (!sessionId || historyLoadedRef.current === sessionId) return
    historyLoadedRef.current = sessionId

    // cancelled 标记：sessionId 切换时取消旧请求的回调，防止竞态覆盖新 session 状态
    let cancelled = false

    const loadHistory = async () => {
      try {
        // 1. 加载历史消息 → 注入 chatStore
        const { messages: rawMsgs } = await getSessionMessages(sessionId)
        if (cancelled) return   // sessionId 已切换，丢弃旧结果
        if (rawMsgs && rawMsgs.length > 0) {
          const chatMsgs = rawMsgs
            .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content?.trim())
            .map((m) => ({
              id:        m.id,
              role:      m.role as 'user' | 'assistant',
              content:   m.content ?? '',
              createdAt: m.created_at,
              ...(m.role === 'assistant' ? { kind: 'turn' as const } : {}),
            }))
          if (chatMsgs.length > 0) {
            // 再次检查：若 store 已有消息（SSE replay 先到），做 id 去重合并而非全量覆盖
            const existing = useChatStore.getState().messages
            if (existing.length === 0) {
              setMessages(chatMsgs)
            } else {
              const existingIds = new Set(existing.map((m) => m.id))
              const newMsgs = chatMsgs.filter((m) => !existingIds.has(m.id))
              if (newMsgs.length > 0) setMessages([...existing, ...newMsgs])
            }
          }
        }

        // 2. 加载历史 TODO → 注入 chatStore
        const { todos: rawTodos } = await getSessionTodos(sessionId)
        if (cancelled) return   // sessionId 已切换，丢弃旧结果
        if (rawTodos && rawTodos.length > 0) {
          const todoItems = rawTodos.map((t) => ({
            id:     t.id,
            title:  t.title,
            detail: t.detail,
            status: (t.status as 'pending' | 'running' | 'done' | 'failed') ?? 'done',
          }))
          setTodos(todoItems, rawTodos[0]?.summary ?? '', rawTodos[0]?.domain ?? '')
        }
      } catch (err) {
        // 历史加载失败不影响正常使用，静默忽略
        if (!cancelled) console.warn('[EP-Agent] 历史恢复失败:', err)
      }
    }

    loadHistory()
    // cleanup：sessionId 变化时标记取消，防止旧异步回调污染新 session
    return () => { cancelled = true }
  }, [sessionId, setMessages, setTodos])

  // ── SSE 双分发（连接后接收实时事件 + 后端 replay）────────────────────────────
  // 后端 SSE 连接建立时会先 replay abc.updated / message.history / todo.list，
  // 与上面的 HTTP 拉取互为补充（HTTP 更快，SSE replay 兜底）。
  // 注意：SSE 订阅在 resetRuntime/setMessages 之后建立，避免旧事件污染新 session 状态。
  useEffect(() => {
    if (!sessionId) return
    // 先断开旧连接，再建立新连接（顺序保证：reset 先于 subscribe）
    unsubRef.current?.()
    unsubRef.current = null
    // 用 setTimeout(0) 确保本轮 React 批量更新（resetRuntime/setMessages）先执行完毕，
    // 再建立 SSE 连接，避免 replay 事件在 store 清空前到达
    const timer = setTimeout(() => {
      unsubRef.current = subscribeToSession(sessionId, (event) => {
        handleSSEEvent(event)
        chatHandleSSE(event)
      })
    }, 0)
    return () => {
      clearTimeout(timer)
      unsubRef.current?.()
      unsubRef.current = null
    }
  }, [sessionId, handleSSEEvent, chatHandleSSE])

  // 谱子更新时自动切换到预览 Tab
  useEffect(() => {
    if (abcNotation) { setCenterTab('preview'); setActiveFileId(null) }
  }, [abcNotation])

  const handleResizeDrag = useCallback((dx: number) => {
    setChatWidth(w => Math.max(CHAT_MIN_W, Math.min(CHAT_MAX_W, w - dx)))
  }, [])

  const handleFilePreview = useCallback((file: WorkspaceFile) => {
    setActiveFileId(file.id); setCenterTab('preview')
  }, [])

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans overflow-hidden">

      {/* ── 顶栏 ── */}
      <header className="h-10 bg-white border-b border-gray-100 flex items-center px-4 gap-3 shrink-0 shadow-sm shadow-gray-50 z-10">
        {/* 侧边栏折叠按钮 */}
        <button
          onClick={() => setSidebarOpen(v => !v)}
          className="w-6 h-6 flex items-center justify-center rounded-md hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors shrink-0"
          title={sidebarOpen ? '折叠侧边栏' : '展开侧边栏'}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d={sidebarOpen ? 'M11 19l-7-7 7-7M18 19l-7-7 7-7' : 'M13 5l7 7-7 7M6 5l7 7-7 7'} />
          </svg>
        </button>

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
            <span className="text-xs text-orange-500 font-medium truncate max-w-[180px]">{score.meta.title}</span>
            <span className="text-xs text-gray-300 hidden sm:inline">
              {score.meta.key} · ♩={Math.round(score.meta.bpm)}
            </span>
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          {/* 角色切换按钮（顶栏常驻，点击弹出角色面板）*/}
          <button
            onClick={() => setShowRoleSwitcher(v => !v)}
            title="切换专家角色"
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-orange-50 border border-orange-100 hover:bg-orange-100 transition-colors shrink-0"
          >
            <span className="text-sm">{activeRoleIcon}</span>
            <span className="text-[11px] font-medium text-orange-600 hidden sm:inline max-w-[80px] truncate">{activeRoleName}</span>
            <svg className="w-3 h-3 text-orange-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          <div className="flex items-center gap-1.5">
            <span className={['w-1.5 h-1.5 rounded-full', sessionId ? 'bg-green-400' : 'bg-gray-300'].join(' ')} />
            <span className="text-[10px] text-gray-400 font-mono hidden sm:inline">
              {sessionTitle
                ? sessionTitle.length > 12 ? sessionTitle.slice(0, 12) + '…' : sessionTitle
                : sessionId ? sessionId.slice(0, 8) + '…' : '未连接'
              }
            </span>
          </div>
          <Link href="/"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-orange-50">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            返回
          </Link>
        </div>
      </header>

      {/* ── 角色切换面板（顶栏按钮触发，fixed 定位避免被 overflow:hidden 裁剪）── */}
      {showRoleSwitcher && sessionId && (
        <div className="fixed inset-0 z-[300] flex items-start justify-center pt-14" onClick={() => setShowRoleSwitcher(false)}>
          <div onClick={e => e.stopPropagation()}>
            <RoleSwitcher
              sessionId={sessionId}
              currentRoleId={activeRoleId}
              compact={false}
              onClose={() => setShowRoleSwitcher(false)}
              onRoleChange={(role, greeting) => {
                setShowRoleSwitcher(false)
                useChatStore.getState().setActiveRole(role.id, role.name, role.icon, role.color)
                if (greeting) useChatStore.getState().addGreetingMessage(greeting, role.name, role.icon)
              }}
            />
          </div>
        </div>
      )}

      {/* ── 主体 ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧：工作区侧边栏（可折叠）── */}
        {/* 工作区图标栏始终可见（即使内容区折叠），保证用户可以随时展开 */}
        <aside
          className={[
            'flex overflow-hidden shrink-0 bg-white transition-all duration-200',
            sidebarOpen ? 'w-14 border-r border-gray-100' : 'w-0',
          ].join(' ')}
        >
          <WorkspaceSidebar />
        </aside>

        {/* ── 谱子文件树（固定宽度，在侧边栏右侧）── */}
        <aside className="w-48 bg-white border-r border-gray-100 flex flex-col overflow-hidden shrink-0">
          <div className="flex-1 overflow-y-auto">
            <WorkspaceExplorer
              score={score}
              sessionId={sessionId}
              onPreview={handleFilePreview}
              activeFileId={activeFileId}
            />
          </div>
          <div className="border-t border-gray-50 px-3 py-2.5 shrink-0">
            <p className="text-[10px] text-gray-300 leading-relaxed text-center">
              在右侧对话框粘贴 Sky JSON<br />即可自动加载谱子
            </p>
          </div>
        </aside>

        {/* ── 中央：Tab 预览区 ── */}
        <main className="flex-1 flex flex-col overflow-hidden bg-white border-r border-gray-100 min-w-0">
          <div className="flex items-center border-b border-gray-100 shrink-0 bg-gray-50/50">
            {CENTER_TABS.map((tab) => (
              <button key={tab.id}
                onClick={() => { setCenterTab(tab.id); setActiveFileId(null) }}
                className={['flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-all border-b-2 -mb-px',
                  centerTab === tab.id && activeFileId === null
                    ? 'border-orange-500 text-orange-600 bg-white'
                    : 'border-transparent text-gray-400 hover:text-gray-600 hover:bg-white/60',
                ].join(' ')}
              >
                <span>{tab.icon}</span><span>{tab.label}</span>
              </button>
            ))}
            {activeFileId && (
              <div className="flex items-center gap-1 px-3 py-2.5 border-b-2 border-orange-500 bg-white text-orange-600 text-xs font-medium ml-1">
                <span>{buildWorkspaceFiles(score).find(f => f.id === activeFileId)?.icon}</span>
                <span>{buildWorkspaceFiles(score).find(f => f.id === activeFileId)?.name}</span>
                <button onClick={() => setActiveFileId(null)} className="ml-1 text-orange-300 hover:text-orange-600">✕</button>
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {activeFileId === 'abc' && abcNotation && (
              <div className="p-5">
                <pre className="p-4 bg-gray-50 rounded-2xl text-xs text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 leading-relaxed max-h-[calc(100vh-200px)] overflow-y-auto">
                  {abcNotation}
                </pre>
              </div>
            )}
            {activeFileId === 'json' && score && (
              <div className="p-5 space-y-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Sky JSON 结构预览</span>
                  <span className="text-[10px] text-gray-300 font-mono">{score.meta.note_count} 个音符</span>
                </div>
                {/* 元数据卡片 */}
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: '标题',  value: score.meta.title },
                    { label: '调号',  value: score.meta.key },
                    { label: 'BPM',   value: String(Math.round(score.meta.bpm)) },
                    { label: '音符数', value: String(score.meta.note_count) },
                  ].map(({ label, value }) => (
                    <div key={label} className="px-3 py-2 bg-gray-50 rounded-xl border border-gray-100 flex justify-between items-center">
                      <span className="text-[11px] text-gray-400">{label}</span>
                      <span className="text-[11px] font-mono font-medium text-gray-700 truncate max-w-[100px]">{value}</span>
                    </div>
                  ))}
                </div>
                {/* JSON 原始预览（截断显示前 60 行） */}
                <div>
                  <p className="text-[10px] text-gray-300 mb-1.5">原始 JSON（前 60 行）</p>
                  <pre className="p-3.5 bg-gray-50 rounded-2xl text-[11px] text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 leading-relaxed max-h-[calc(100vh-340px)] overflow-y-auto">
                    {JSON.stringify(score, null, 2).split('\n').slice(0, 60).join('\n')}
                    {JSON.stringify(score, null, 2).split('\n').length > 60 && '\n… (更多内容请下载文件查看)'}
                  </pre>
                </div>
                <p className="text-[10px] text-gray-300 text-center">完整文件请在「导出」Tab 下载</p>
              </div>
            )}
            {activeFileId === 'midi' && score && (
              <div className="p-5 space-y-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">MIDI 文件信息</span>
                  <span className="text-[10px] text-gray-300 font-mono">.mid</span>
                </div>
                {/* MIDI 统计卡片 */}
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: '曲目标题', value: score.meta.title },
                    { label: '调号',    value: score.meta.key },
                    { label: 'BPM',     value: String(Math.round(score.meta.bpm)) },
                    { label: '总音符数', value: String(score.meta.note_count) },
                    { label: '格式',    value: 'MIDI Type 0' },
                    { label: '乐器',    value: 'Piano (GM #1)' },
                  ].map(({ label, value }) => (
                    <div key={label} className="px-3 py-2 bg-gray-50 rounded-xl border border-gray-100 flex justify-between items-center">
                      <span className="text-[11px] text-gray-400">{label}</span>
                      <span className="text-[11px] font-mono font-medium text-gray-700 truncate max-w-[100px]">{value}</span>
                    </div>
                  ))}
                </div>
                {/* ABC 源码（MIDI 是二进制，展示 ABC 作为参考） */}
                <div>
                  <p className="text-[10px] text-gray-300 mb-1.5">对应 ABC 记谱（MIDI 由此生成）</p>
                  <pre className="p-3.5 bg-gray-50 rounded-2xl text-[11px] text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 leading-relaxed max-h-[calc(100vh-380px)] overflow-y-auto">
                    {abcNotation ?? '（暂无 ABC 数据）'}
                  </pre>
                </div>
                <p className="text-[10px] text-gray-300 text-center">MIDI 为二进制文件，请在「导出」Tab 下载后用 DAW 打开</p>
              </div>
            )}
            {activeFileId && activeFileId !== 'abc' && !score && (
              <div className="flex flex-col items-center justify-center h-full text-center p-8 space-y-3">
                <span className="text-3xl opacity-30">{activeFileId === 'json' ? '🎮' : '🎹'}</span>
                <p className="text-sm text-gray-500">切换到「导出」Tab 下载文件</p>
              </div>
            )}
            {!activeFileId && centerTab === 'preview' && (
              abcNotation ? (
                <div className="p-5">
                  <details className="mb-4 group">
                    <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none flex items-center gap-1.5 list-none">
                      <svg className="w-2.5 h-2.5 transition-transform group-open:rotate-90 text-gray-300"
                        fill="none" stroke="currentColor" viewBox="0 0 24 24">
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
                  <div className="w-16 h-16 bg-gradient-to-br from-gray-50 to-gray-100 rounded-2xl flex items-center justify-center text-4xl shadow-inner">🎼</div>
                  <div className="space-y-1.5">
                    <p className="text-sm font-semibold text-gray-600">在右侧对话框开始</p>
                    <p className="text-xs text-gray-400 max-w-xs leading-relaxed">粘贴 Sky JSON 谱子，或直接告诉 AI 你想做什么</p>
                  </div>
                  <div className="flex flex-col gap-2 text-xs text-gray-400 max-w-xs">
                    {[
                      ['💬', '直接说话', '「生成中国风配乐」「升高八度」'],
                      ['📋', '粘贴 JSON', '自动识别 Sky 谱子并转换'],
                      ['🎵', '附加音频', '粘贴 MP3 克隆你的声音'],
                    ].map(([icon, t, desc]) => (
                      <div key={t} className="flex items-start gap-2 px-3 py-2 bg-gray-50 rounded-xl border border-gray-100 text-left">
                        <span className="text-base shrink-0">{icon}</span>
                        <div><p className="font-medium text-gray-600">{t}</p><p className="text-gray-400 text-[11px]">{desc}</p></div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}
            {!activeFileId && centerTab === 'logs' && <div className="p-4"><PipelineStatus /></div>}
            {!activeFileId && centerTab === 'export' && <ExportPanel />}
          </div>
        </main>

        {/* ── 可拖拽分隔条 ── */}
        <ResizeDivider onDrag={handleResizeDrag} />

        {/* ── 右侧：对话面板 ── */}
        <aside style={{ width: chatWidth }} className="flex flex-col overflow-hidden shrink-0 bg-white">
          <details className="border-b border-gray-100 group shrink-0">
            <summary className="px-3 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider cursor-pointer hover:text-gray-600 flex items-center gap-1.5 select-none list-none transition-colors hover:bg-gray-50">
              <svg className="w-3 h-3 transition-transform group-open:rotate-90 text-gray-300 shrink-0"
                fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              🎵 音频生成
              <span className="ml-auto text-[9px] text-gray-300 font-normal normal-case tracking-normal">点击展开</span>
            </summary>
            <div className="max-h-72 overflow-y-auto border-t border-gray-50"><AudioPanel /></div>
          </details>
          <div className="flex-1 overflow-hidden"><ChatPanel /></div>
        </aside>
      </div>
    </div>
  )
}
