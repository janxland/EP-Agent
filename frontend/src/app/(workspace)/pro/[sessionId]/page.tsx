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
import { subscribeToSession, getSessionMessages, getSessionTodos } from '@/shared/lib/api'
import { AudioPanel } from '@/widgets/audio-panel/AudioPanel'
import { ChatPanel } from '@/widgets/chat-panel/ChatPanel'
import { WorkspaceFileTree, FILE_PREVIEW_EVENT } from '@/widgets/workspace-sidebar/WorkspaceFileTree'
import { WorkspaceSidebar } from '@/widgets/workspace-sidebar/WorkspaceSidebar'
import { type WorkspaceFile as WsFile } from '@/shared/lib/workspace-files-api'
import { PreviewTabs } from '@/features/preview/components/PreviewTabs'
import { previewTabs } from '@/features/preview/store/preview-tabs.store'
import Link from 'next/link'

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

// ─── 主页面 ───────────────────────────────────────────────────────────────────

const CHAT_MIN_W = 280
const CHAT_MAX_W = 640
const CHAT_DEFAULT_W = 380

export default function ProSessionPage() {
  const params = useParams()
  const router = useRouter()
  const sessionId = params.sessionId as string

  const { setSessionId, abcNotation, score, handleSSEEvent, reset: resetScore } = useScoreStore()
  const { handleSSEEvent: chatHandleSSE, setMessages, setTodos, resetRuntime, restoreRoleFromSession } = useChatStore(
    (s) => ({
      handleSSEEvent: s.handleSSEEvent,
      setMessages: s.setMessages,
      setTodos: s.setTodos,
      resetRuntime: s.resetRuntime,
      restoreRoleFromSession: s.restoreRoleFromSession,
    })
  )
  const { restoreFromSessionId, setActiveSessionId, _pendingNavigateSessionId, clearPendingNavigate, activeSessions } = useWorkspaceStore()

  // 用 useMemo 缓存 activeSessions 结果，避免每次渲染都重新遍历 workspaces
  // activeSessions() 依赖 workspaces，workspaces 变化时自动重新计算
  const { workspaces } = useWorkspaceStore()
  const activeSessionsList = useMemo(() => activeSessions(), [activeSessions, workspaces])

  // 从 workspace store 读取当前 session 的标题（比 sessionId 截断更友好）
  const sessionTitle = activeSessionsList.find((s) => s.id === sessionId)?.title ?? null

  const [chatWidth, setChatWidth]       = useState(CHAT_DEFAULT_W)
  const [sidebarOpen, setSidebarOpen]   = useState(true)
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

  // ── Session 切换：原子初始化（清空 → 加载历史 → 建立 SSE）────────────────────
  //
  // ⚠️ 关键设计：三个步骤必须在同一个 effect 中串行执行，不能拆成多个 effect。
  // 原因：多个 effect 都依赖 [sessionId] 时，React 不保证执行顺序，会导致：
  //   - 旧 session 的历史消息在 setMessages([]) 之后才写入（竞态）
  //   - SSE replay 在 store 清空前到达，污染新 session
  //   - 历史加载请求在 sessionId 已切换后才返回，覆盖新 session 的空状态
  //
  // 正确顺序（严格串行）：
  //   1. 断开旧 SSE
  //   2. 清空 store（同步）
  //   3. 加载新 session 历史（异步，有 cancelled 守卫）
  //   4. 建立新 SSE（在清空和历史加载之后）
  useEffect(() => {
    if (!sessionId) return

    // ── Step 1：断开旧 SSE，防止旧 replay 事件污染新 session ──
    unsubRef.current?.()
    unsubRef.current = null

    // ── Step 2：同步清空所有状态（必须在异步操作前完成）──
    resetScore()
    resetRuntime()
    // 直接调用 store 的 setState，绕过 setMessages 的去重合并逻辑，强制清空
    useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
    historyLoadedRef.current = sessionId   // 提前标记，防止重复加载

    // 同步更新 workspace/session 关联
    setSessionId(sessionId)
    setActiveSessionId(sessionId)
    restoreFromSessionId(sessionId)
    restoreRoleFromSession(sessionId)

    // ── Step 3：异步加载历史（cancelled 守卫防止旧请求污染）──
    let cancelled = false

    const loadHistory = async () => {
      try {
        // 3a. 加载历史消息
        const { messages: rawMsgs } = await getSessionMessages(sessionId)
        if (cancelled) return
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
          if (chatMsgs.length > 0 && !cancelled) {
            // 强制覆盖（此时 store 已清空，不需要去重合并）
            useChatStore.setState({ messages: chatMsgs })
          }
        }

        // 3b. 加载历史 TODO
        const { todos: rawTodos } = await getSessionTodos(sessionId)
        if (cancelled) return
        if (rawTodos && rawTodos.length > 0) {
          const todoItems = rawTodos.map((t) => ({
            id:     t.id,
            title:  t.title,
            detail: t.detail,
            status: (t.status as 'pending' | 'running' | 'done' | 'failed' | 'skipped') ?? 'done',
          }))
          if (!cancelled) setTodos(todoItems, rawTodos[0]?.summary ?? '', rawTodos[0]?.domain ?? '')
        }
      } catch (err) {
        if (!cancelled) console.warn('[EP-Agent] 历史恢复失败:', err)
      }

      // ── Step 4：历史加载完成后再建立 SSE（避免 replay 与 HTTP 历史竞态）──
      // 用 setTimeout(0) 确保本轮 React 批量更新先完成
      if (!cancelled) {
        setTimeout(() => {
          if (cancelled) return
          unsubRef.current = subscribeToSession(sessionId, (event) => {
            handleSSEEvent(event)
            chatHandleSSE(event)
          })
        }, 0)
      }
    }

    loadHistory()

    return () => {
      cancelled = true
      unsubRef.current?.()
      unsubRef.current = null
    }
  // ⚠️ 依赖数组只放「真正会导致需要重新初始化」的值：sessionId（路由变化）
  // handleSSEEvent / chatHandleSSE 是 zustand store 方法，引用稳定，但放入依赖
  // 会在父组件每次渲染时触发不必要的 effect 重跑（清空 + 重新加载历史）。
  // 其余 setter 函数同理，用 useRef 或直接从 store 读取，不放入依赖。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // 监听工作区文件预览事件 → dispatch 到 preview-tabs store
  useEffect(() => {
    const handler = (e: Event) => {
      const file = (e as CustomEvent).detail as WsFile & { workspaceId: string }
      previewTabs.openFile(file, file.workspaceId)
    }
    window.addEventListener(FILE_PREVIEW_EVENT, handler)
    return () => window.removeEventListener(FILE_PREVIEW_EVENT, handler)
  }, [])

  // ABC 更新时自动打开/更新 abc 标签
  useEffect(() => {
    if (abcNotation) {
      previewTabs.updateAbc(abcNotation, score?.meta?.title)
    }
  }, [abcNotation, score?.meta?.title])

  const handleResizeDrag = useCallback((dx: number) => {
    setChatWidth(w => Math.max(CHAT_MIN_W, Math.min(CHAT_MAX_W, w - dx)))
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



        <div className="ml-auto flex items-center gap-2">
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

        {/* ── 左侧面板：工作区文件树 ── */}
        <aside className="flex bg-white border-r border-gray-100 shrink-0 overflow-hidden" style={{ width: 192 }}>
          <div className="w-full flex flex-col overflow-hidden">
            <WorkspaceFileTree />
          </div>
        </aside>

        {/* ── 中央：多标签预览区（解耦，状态由 preview-tabs.store 管理）── */}
        <main className="flex-1 flex flex-col overflow-hidden bg-white border-r border-gray-100 min-w-0">
          <PreviewTabs />
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
