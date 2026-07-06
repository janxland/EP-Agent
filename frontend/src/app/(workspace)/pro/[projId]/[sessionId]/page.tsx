'use client'

/**
 * /pro/[projId]/[sessionId] — 专业模式主工作台（v2 无刷新切换版）
 *
 * 架构升级（v2）：
 *   - page.tsx 只做静态布局 + projId/sessionId 写入 store
 *   - Session 初始化（清空/SSE订阅/历史加载）已下沉到 ChatPanel 内部
 *   - 切换 session 时只有 ChatPanel 消息列表区域重新加载，其他组件完全不感知
 *   - URL 仍然更新（保留书签/分享能力），但通过 store 驱动而非整页重渲染
 */

import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
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

  const {
    setActiveProjectId,
    setActiveSessionId,
    _pendingNavigateSessionId,
    clearPendingNavigate,
    activeWorkspaceId,
    workspaces,
    activeSessions,
  } = useWorkspaceStore()

  const activeSessionsList = activeSessions()
  const sessionTitle = activeSessionsList.find((s) => s.id === sessionId)?.title ?? null

  const [chatWidth, setChatWidth]     = useState(CHAT_DEFAULT_W)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const routerRef = useRef(router)
  useEffect(() => { routerRef.current = router })

  // ── projId 写入 store（消灭首次发消息 project_id 为空的时序问题）────────────
  // 加 guard：只在 projId 真正变化时才写入，避免 router.replace 同路由重挂载时
  // 重复触发 setActiveProjectId，进而引发 WorkspaceFileTree 不必要的文件树刷新
  const prevProjIdRef = useRef<string>('')
  useEffect(() => {
    if (projId && projId !== prevProjIdRef.current) {
      prevProjIdRef.current = projId
      setActiveProjectId(projId)
    }
  }, [projId, setActiveProjectId])

  // ── sessionId 写入 store（供 ChatPanel 监听，触发消息区域重载）──────────────
  // 注意：这里只更新 store，ChatPanel 内部的 useEffect([activeSessionId]) 负责实际初始化
  // 同样加 guard：只在 sessionId 真正变化时才写入，防止重挂载时重复触发
  const prevSessionIdRef = useRef<string>('')
  useEffect(() => {
    if (sessionId && sessionId !== prevSessionIdRef.current) {
      prevSessionIdRef.current = sessionId
      setActiveSessionId(sessionId)
    }
  }, [sessionId, setActiveSessionId])

  // ── 监听删除后的跳转信号 ──────────────────────────────────────────────────────
  useEffect(() => {
    if (_pendingNavigateSessionId === undefined) return
    clearPendingNavigate()
    if (_pendingNavigateSessionId) {
      const targetProjId = workspaces
        .flatMap((w) => w.projects ?? [])
        .find((p) => p.sessions?.some((s) => s.id === _pendingNavigateSessionId))?.id
        ?? projId
      routerRef.current.replace(`/pro/${targetProjId}/${_pendingNavigateSessionId}`)
    } else {
      routerRef.current.replace('/pro')
    }
  }, [_pendingNavigateSessionId, clearPendingNavigate, projId, workspaces])

  // ── 监听工作区文件预览事件 ────────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: Event) => {
      const file = (e as CustomEvent).detail as WsFile & { workspaceId: string; projectId?: string }
      const wsId = file.workspaceId || useWorkspaceStore.getState().activeWorkspaceId || ''
      const pId  = file.projectId   || projId || useWorkspaceStore.getState().activeProjectId || undefined
      previewTabs.openFile(file, wsId, pId)
    }
    window.addEventListener(FILE_PREVIEW_EVENT, handler)
    return () => window.removeEventListener(FILE_PREVIEW_EVENT, handler)
  }, [projId])

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
