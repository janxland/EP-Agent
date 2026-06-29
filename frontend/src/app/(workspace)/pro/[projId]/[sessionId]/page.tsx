'use client'

/**
 * /pro/[projId]/[sessionId] — 专业模式主工作台
 *
 * 相比旧版 /pro/[sessionId]：
 *   - URL 中直接包含 projId，无需异步查询即可定位文件系统路径
 *   - restoreFromSessionId 仍保留作为兜底（处理旧书签/直接输入 URL）
 *   - projId 从 URL 读取后立即写入 store，彻底消灭 activeProjectId 为空的时序问题
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
  const params    = useParams()
  const router    = useRouter()
  const projId    = params.projId    as string
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
  const {
    restoreFromSessionId,
    setActiveSessionId,
    setActiveProjectId,
    _pendingNavigateSessionId,
    clearPendingNavigate,
    activeSessions,
    activeWorkspaceId,
  } = useWorkspaceStore()

  const { workspaces } = useWorkspaceStore()
  const activeSessionsList = useMemo(() => activeSessions(), [activeSessions, workspaces])
  const sessionTitle = activeSessionsList.find((s) => s.id === sessionId)?.title ?? null

  const [chatWidth, setChatWidth]     = useState(CHAT_DEFAULT_W)
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const historyLoadedRef = useRef<string | null>(null)
  const unsubRef = useRef<(() => void) | null>(null)

  const routerRef = useRef(router)
  useEffect(() => { routerRef.current = router })

  // ── 监听删除后的跳转信号 ──────────────────────────────────────────────────────
  useEffect(() => {
    if (_pendingNavigateSessionId === undefined) return
    clearPendingNavigate()
    if (_pendingNavigateSessionId) {
      // 找到目标 session 的 projId
      const targetProjId = workspaces
        .flatMap((w) => w.projects ?? [])
        .find((p) => p.sessions?.some((s) => s.id === _pendingNavigateSessionId))?.id
        ?? projId  // 兜底：同项目
      routerRef.current.replace(`/pro/${targetProjId}/${_pendingNavigateSessionId}`)
    } else {
      routerRef.current.replace('/pro')
    }
  }, [_pendingNavigateSessionId, clearPendingNavigate, projId, workspaces])

  // ── 关键修复：URL 中的 projId 直接写入 store，消灭时序问题 ─────────────────────
  // 旧版只有 sessionId，需要异步查询才能知道 projId，导致首次发消息时 project_id 为空。
  // 新版 URL 直接携带 projId，同步写入，彻底消灭这个时序窗口。
  useEffect(() => {
    if (projId) setActiveProjectId(projId)
  }, [projId, setActiveProjectId])

  // ── Session 切换：原子初始化（清空 → 加载历史 → 建立 SSE）────────────────────
  useEffect(() => {
    if (!sessionId) return

    unsubRef.current?.()
    unsubRef.current = null

    resetScore()
    resetRuntime()
    useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
    historyLoadedRef.current = sessionId

    setSessionId(sessionId)
    setActiveSessionId(sessionId)
    // projId 已在上方 effect 写入，此处 restoreFromSessionId 作为兜底（补全 wsId）
    restoreFromSessionId(sessionId)
    restoreRoleFromSession(sessionId)

    let cancelled = false

    const loadHistory = async () => {
      try {
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
            useChatStore.setState({ messages: chatMsgs })
          }
        }

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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // 监听工作区文件预览事件
  useEffect(() => {
    const handler = (e: Event) => {
      const file = (e as CustomEvent).detail as WsFile & { workspaceId: string; projectId?: string }
      const wsId   = file.workspaceId || useWorkspaceStore.getState().activeWorkspaceId || ''
      const pId    = file.projectId   || projId || useWorkspaceStore.getState().activeProjectId || undefined
      previewTabs.openFile(file, wsId, pId)
    }
    window.addEventListener(FILE_PREVIEW_EVENT, handler)
    return () => window.removeEventListener(FILE_PREVIEW_EVENT, handler)
  }, [projId])

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

        {/* 面包屑：工作区 → 项目 → 对话 */}
        {activeWorkspaceId && (
          <>
            <span className="text-gray-200 text-xs">│</span>
            <Link
              href={`/pro/workspace/${activeWorkspaceId}`}
              className="text-xs text-gray-400 hover:text-orange-500 transition-colors font-mono"
              title="返回工作区"
            >
              {activeWorkspaceId.slice(0, 8)}…
            </Link>
            {projId && (
              <>
                <span className="text-gray-200 text-xs">/</span>
                <span className="text-xs text-gray-500 font-mono" title={projId}>
                  {projId.slice(0, 8)}…
                </span>
              </>
            )}
          </>
        )}

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
        <aside className={['flex overflow-hidden shrink-0 bg-white transition-all duration-200', sidebarOpen ? 'w-14 border-r border-gray-100' : 'w-0'].join(' ')}>
          <WorkspaceSidebar />
        </aside>

        <aside className="flex bg-white border-r border-gray-100 shrink-0 overflow-hidden" style={{ width: 192 }}>
          <div className="w-full flex flex-col overflow-hidden">
            <WorkspaceFileTree />
          </div>
        </aside>

        <main className="flex-1 flex flex-col overflow-hidden bg-white border-r border-gray-100 min-w-0">
          <PreviewTabs />
        </main>

        <ResizeDivider onDrag={handleResizeDrag} />

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
