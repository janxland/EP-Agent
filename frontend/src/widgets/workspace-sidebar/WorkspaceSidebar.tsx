'use client'

/**
 * WorkspaceSidebar.tsx — 图标级工作区侧边栏
 *
 * 设计：参考 coding 的竖排图标模式
 *   - 左侧窄栏：每个工作区显示为圆形图标（首字母），点击激活
 *   - 右侧悬浮面板：展示当前工作区的对话列表（hover 保持 / 点击外侧关闭）
 *   - 删除：await store 方法 + 监听 _pendingNavigateSessionId 自动跳转
 *   - 双击图标重命名（内联 input）
 */

import {
  useCallback, useEffect, useRef, useState,
  type MouseEvent, type KeyboardEvent,
} from 'react'
import { useRouter } from 'next/navigation'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import type { WorkspaceDto, SessionInfoDto } from '@/features/workspace/store/workspace.store'

// ─── 图标 ──────────────────────────────────────────────────────────────────────
function Icon({ path, className = 'w-4 h-4' }: { path: string; className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d={path} />
    </svg>
  )
}
const ICONS = {
  plus:    'M12 4v16m8-8H4',
  trash:   'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16',
  chat:    'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
  check:   'M5 13l4 4L19 7',
  x:       'M6 18L18 6M6 6l12 12',
  music:   'M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3',
  pencil:  'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z',
}

// ─── 对话项 ────────────────────────────────────────────────────────────────────
function SessionItem({
  session, isActive, onSelect, onDelete, onRename,
}: {
  session: SessionInfoDto
  isActive: boolean
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing]             = useState(false)
  const [editTitle, setEditTitle]         = useState(session.title || '新对话')
  const timer     = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef  = useRef<HTMLInputElement>(null)

  const handleDelete = useCallback((e: MouseEvent) => {
    e.stopPropagation()
    if (confirmDelete) {
      void Promise.resolve(onDelete(session.id))
      setConfirmDelete(false)
    } else {
      setConfirmDelete(true)
      timer.current = setTimeout(() => setConfirmDelete(false), 2500)
    }
  }, [confirmDelete, onDelete, session.id])

  const submitRename = useCallback(() => {
    const t = editTitle.trim()
    if (t && t !== session.title) onRename(session.id, t)
    setEditing(false)
  }, [editTitle, session.id, session.title, onRename])

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const meta = [
    session.score_key,
    session.score_bpm ? `♩${Math.round(session.score_bpm)}` : null,
  ].filter(Boolean).join(' · ')

  return (
    <div
      onClick={() => !editing && onSelect(session.id)}
      className={[
        'group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all duration-150',
        isActive
          ? 'bg-orange-50 border border-orange-100'
          : 'hover:bg-gray-50 border border-transparent',
      ].join(' ')}
    >
      <Icon
        path={ICONS.chat}
        className={['w-3.5 h-3.5 shrink-0', isActive ? 'text-orange-400' : 'text-gray-300 group-hover:text-gray-400'].join(' ')}
      />
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={inputRef}
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={submitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitRename()
              if (e.key === 'Escape') { setEditTitle(session.title || '新对话'); setEditing(false) }
              e.stopPropagation()
            }}
            onClick={(e) => e.stopPropagation()}
            className="w-full text-[11px] bg-white border border-orange-300 rounded px-1 py-0.5 outline-none text-gray-700"
          />
        ) : (
          <p
            onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); setEditTitle(session.title || '新对话') }}
            title="双击重命名"
            className={['text-[11px] font-medium truncate', isActive ? 'text-orange-700' : 'text-gray-600'].join(' ')}
          >
            {session.title || '新对话'}
          </p>
        )}
        {meta && <p className="text-[9px] text-gray-300 font-mono mt-0.5">{meta}</p>}
      </div>
      {/* 重命名按钮 */}
      {!editing && (
        <button
          onClick={(e) => { e.stopPropagation(); setEditing(true); setEditTitle(session.title || '新对话') }}
          title="重命名"
          className="shrink-0 w-5 h-5 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 text-gray-300 hover:text-orange-400 hover:bg-orange-50 transition-all"
        >
          <Icon path={ICONS.pencil} className="w-3 h-3" />
        </button>
      )}
      {/* 删除按钮 */}
      {!editing && (
        <button
          onClick={handleDelete}
          title={confirmDelete ? '再次点击确认删除' : '删除对话'}
          className={[
            'shrink-0 w-5 h-5 flex items-center justify-center rounded transition-all',
            confirmDelete
              ? 'opacity-100 text-red-500 bg-red-50'
              : 'opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-400 hover:bg-red-50',
          ].join(' ')}
        >
          <Icon path={confirmDelete ? ICONS.check : ICONS.trash} className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}

// ─── 悬浮面板（工作区详情）────────────────────────────────────────────────────
function WorkspacePanel({
  workspace, activeSessionId,
  onSelectSession, onDeleteSession, onCreateSession,
  onRename, onDelete, onClose, onRenameSession,
}: {
  workspace: WorkspaceDto
  activeSessionId?: string | null
  onSelectSession: (sessionId: string, wsId: string) => void
  onDeleteSession: (sessionId: string) => void
  onCreateSession: (wsId: string) => void
  onRename: (wsId: string, name: string) => void
  onDelete: (wsId: string) => void
  onClose: () => void
  onRenameSession: (sessionId: string, title: string) => void
}) {
  const [editing, setEditing]       = useState(false)
  const [editName, setEditName]     = useState(workspace.name)
  const [confirmDel, setConfirmDel] = useState(false)
  const inputRef                    = useRef<HTMLInputElement>(null)
  const delTimer                    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const panelRef                    = useRef<HTMLDivElement>(null)

  // 点击面板外关闭
  // 用 mousedown 捕获阶段注册，通过 openedByRef 标记忽略打开面板的那次点击，
  // 避免 50ms 延迟导致快速点击时 handler 未注册而面板卡住的问题。
  const outsideHandlerRef = useRef<((e: globalThis.MouseEvent) => void) | null>(null)
  const openedByClickRef  = useRef(true)   // 面板刚打开时跳过第一次 mousedown
  useEffect(() => {
    openedByClickRef.current = true
    const handler = (e: globalThis.MouseEvent) => {
      // 跳过打开面板的那次点击（同帧触发）
      if (openedByClickRef.current) {
        openedByClickRef.current = false
        return
      }
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    outsideHandlerRef.current = handler
    document.addEventListener('mousedown', handler)
    return () => {
      document.removeEventListener('mousedown', handler)
      outsideHandlerRef.current = null
    }
  }, [onClose])

  useEffect(() => () => { if (delTimer.current) clearTimeout(delTimer.current) }, [])

  const submitRename = useCallback(() => {
    const name = editName.trim()
    if (name && name !== workspace.name) onRename(workspace.id, name)
    setEditing(false)
  }, [editName, workspace.id, workspace.name, onRename])

  const handleRenameKey = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') submitRename()
    if (e.key === 'Escape') { setEditName(workspace.name); setEditing(false) }
  }, [submitRename, workspace.name])

  const handleDelete = useCallback((e: MouseEvent) => {
    e.stopPropagation()
    if (confirmDel) {
      void Promise.resolve(onDelete(workspace.id))
    } else {
      setConfirmDel(true)
      delTimer.current = setTimeout(() => setConfirmDel(false), 2500)
    }
  }, [confirmDel, onDelete, workspace.id])

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const sessions = workspace.sessions ?? []

  return (
    <div
      ref={panelRef}
      className="fixed left-14 top-0 z-[200] w-56 bg-white rounded-xl shadow-xl border border-gray-100 flex flex-col overflow-hidden"
      style={{ maxHeight: '80vh' }}
    >
      {/* 面板头部 */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-gray-100 bg-gray-50/60 shrink-0">
        {editing ? (
          <input
            ref={inputRef}
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onBlur={submitRename}
            onKeyDown={handleRenameKey}
            className="flex-1 text-[12px] font-semibold bg-white border border-orange-300 rounded px-1.5 py-0.5 outline-none text-gray-700 min-w-0"
          />
        ) : (
          <span
            onDoubleClick={() => { setEditing(true); setEditName(workspace.name) }}
            title="双击重命名"
            className="flex-1 text-[12px] font-semibold text-gray-700 truncate cursor-default select-none"
          >
            {workspace.name}
          </span>
        )}
        <div className="flex items-center gap-0.5 shrink-0">
          {/* 重命名 */}
          <button
            onClick={() => { setEditing(true); setEditName(workspace.name) }}
            title="重命名"
            className="w-6 h-6 flex items-center justify-center rounded hover:bg-gray-200 text-gray-300 hover:text-gray-500 transition-colors"
          >
            <Icon path={ICONS.pencil} className="w-3 h-3" />
          </button>
          {/* 删除工作区 */}
          <button
            onClick={handleDelete}
            title={confirmDel ? '再次点击确认删除' : '删除工作区'}
            className={[
              'w-6 h-6 flex items-center justify-center rounded transition-colors',
              confirmDel ? 'text-red-500 bg-red-50' : 'text-gray-300 hover:text-red-400 hover:bg-red-50',
            ].join(' ')}
          >
            <Icon path={confirmDel ? ICONS.check : ICONS.trash} className="w-3 h-3" />
          </button>
          {/* 关闭 */}
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded hover:bg-gray-200 text-gray-300 hover:text-gray-500 transition-colors"
          >
            <Icon path={ICONS.x} className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* 对话列表 */}
      <div className="flex-1 overflow-y-auto p-1.5">
        {sessions.length === 0 ? (
          <div className="py-6 text-center">
            <p className="text-[10px] text-gray-300">暂无对话</p>
          </div>
        ) : (
          sessions.map((sess) => (
            <SessionItem
              key={sess.id}
              session={sess}
              isActive={activeSessionId === sess.id}
              onSelect={(id) => { onSelectSession(id, workspace.id); onClose() }}
              onDelete={onDeleteSession}
              onRename={onRenameSession}
            />
          ))
        )}
      </div>

      {/* 新建对话 */}
      <div className="border-t border-gray-100 p-1.5 shrink-0">
        <button
          onClick={() => { onCreateSession(workspace.id); onClose() }}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-orange-50 text-orange-400 hover:text-orange-500 transition-colors"
        >
          <Icon path={ICONS.plus} className="w-3.5 h-3.5 shrink-0" />
          <span className="text-[11px] font-medium">新建对话</span>
        </button>
      </div>
    </div>
  )
}

// ─── 工作区图标按钮 ────────────────────────────────────────────────────────────
function WorkspaceIcon({
  workspace, isActive, onClick,
}: {
  workspace: WorkspaceDto
  isActive: boolean
  onClick: () => void
}) {
  // 取名称首字（中文取第一个字，英文取首字母大写）
  const label = workspace.name.trim().charAt(0).toUpperCase() || '?'

  // 根据 id hash 生成固定颜色（避免每次刷新变色）
  const colors = [
    'bg-orange-100 text-orange-600 border-orange-200',
    'bg-sky-100 text-sky-600 border-sky-200',
    'bg-emerald-100 text-emerald-600 border-emerald-200',
    'bg-violet-100 text-violet-600 border-violet-200',
    'bg-rose-100 text-rose-600 border-rose-200',
    'bg-amber-100 text-amber-600 border-amber-200',
  ]
  const colorIdx = workspace.id.charCodeAt(0) % colors.length
  const colorCls = colors[colorIdx]

  return (
    <button
      onClick={onClick}
      title={workspace.name}
      className={[
        'relative w-9 h-9 rounded-xl flex items-center justify-center text-[13px] font-bold border transition-all duration-150',
        colorCls,
        isActive
          ? 'ring-2 ring-orange-300 ring-offset-1 scale-105 shadow-sm'
          : 'hover:scale-105 hover:shadow-sm opacity-70 hover:opacity-100',
      ].join(' ')}
    >
      {label}
      {/* 活跃指示点 */}
      {isActive && (
        <span className="absolute -right-0.5 -bottom-0.5 w-2 h-2 bg-orange-400 rounded-full border border-white" />
      )}
    </button>
  )
}

// ─── 主组件 ───────────────────────────────────────────────────────────────────
export function WorkspaceSidebar() {
  const router = useRouter()
  const {
    workspaces,
    activeWorkspaceId,
    activeSessionId,
    loading,
    error,
    loadWorkspaces,
    createWorkspace,
    renameWorkspace,
    deleteWorkspace,
    createSession,
    renameSession,
    deleteSession,
    setActiveWorkspaceId,
    setActiveSessionId,
    clearError,
  } = useWorkspaceStore()

  // 注意：_pendingNavigateSessionId 的跳转监听由 /pro/[sessionId]/page.tsx 统一处理，
  // 此处不重复监听，避免 clearPendingNavigate() 被调用两次导致跳转丢失。

  const [openWsId, setOpenWsId]   = useState<string | null>(null)
  const [creating, setCreating]   = useState(false)
  const [newWsName, setNewWsName] = useState('')
  const newWsInputRef             = useRef<HTMLInputElement>(null)
  // 防重入标记：WorkspaceSidebar 始终挂载（CSS w-0/w-14 切换），
  // 避免 loadWorkspaces 在每次渲染时重复触发
  const loadedRef = useRef(false)

  // 初始加载（防重入：只加载一次，后续由 CRUD 操作乐观更新）
  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    loadWorkspaces()
  }, [loadWorkspaces])

  // 选择对话
  const handleSelectSession = useCallback((sessionId: string, wsId: string) => {
    setActiveWorkspaceId(wsId)
    setActiveSessionId(sessionId)
    router.push(`/pro/${sessionId}`)
  }, [router, setActiveWorkspaceId, setActiveSessionId])

  // 新建对话（同时激活对应工作区）
  const handleCreateSession = useCallback(async (wsId: string) => {
    try {
      setActiveWorkspaceId(wsId)   // 先激活工作区，避免跳转后 restoreFromSessionId 找不到
      const sess = await createSession(wsId, '新对话')
      router.push(`/pro/${sess.id}`)
    } catch { /* error 已在 store 中设置 */ }
  }, [createSession, router, setActiveWorkspaceId])

  // 重命名对话
  const handleRenameSession = useCallback(async (sessionId: string, title: string) => {
    try {
      await renameSession(sessionId, title)
    } catch { /* error 已在 store 中设置 */ }
  }, [renameSession])

  // 删除对话（SessionItem 内部已有两次点击确认，此处直接执行）
  const handleDeleteSession = useCallback(async (sessionId: string) => {
    try {
      await deleteSession(sessionId)
    } catch { /* error 已在 store 中设置 */ }
  }, [deleteSession])

  // 删除工作区（二次确认 + await + 跳转由 _pendingNavigateSessionId 监听驱动）
  const handleDeleteWorkspace = useCallback(async (wsId: string) => {
    if (!window.confirm('确定删除该工作区？其下所有对话将一并删除且不可恢复。')) return
    setOpenWsId(null)
    try {
      await deleteWorkspace(wsId)
    } catch { /* error 已在 store 中设置 */ }
  }, [deleteWorkspace])

  // 新建工作区
  const handleCreateWorkspace = useCallback(async () => {
    const name = newWsName.trim() || '新工作区'
    try {
      const ws = await createWorkspace(name)
      setCreating(false)
      setNewWsName('')
      setOpenWsId(ws.id)
    } catch { /* error 已在 store 中设置 */ }
  }, [createWorkspace, newWsName])

  const handleNewWsKey = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleCreateWorkspace()
    if (e.key === 'Escape') { setCreating(false); setNewWsName('') }
  }, [handleCreateWorkspace])

  useEffect(() => { if (creating) newWsInputRef.current?.focus() }, [creating])

  const openWorkspace = workspaces.find((w) => w.id === openWsId) ?? null

  return (
    <div className="relative flex flex-col h-full w-14 bg-white border-r border-gray-100 select-none items-center py-3 gap-2">

      {/* Logo / 顶部图标 */}
      <div className="w-9 h-9 rounded-xl bg-orange-50 flex items-center justify-center mb-1 shrink-0">
        <Icon path={ICONS.music} className="w-4 h-4 text-orange-400" />
      </div>

      {/* 分隔线 */}
      <div className="w-6 border-t border-gray-100 shrink-0" />

      {/* 工作区图标列表 */}
      <div className="flex flex-col gap-2 flex-1 overflow-y-auto w-full items-center py-1">
        {loading && workspaces.length === 0 ? (
          <svg className="w-4 h-4 animate-spin text-orange-300 mt-4" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        ) : (
          workspaces.map((ws) => (
            <WorkspaceIcon
              key={ws.id}
              workspace={ws}
              isActive={activeWorkspaceId === ws.id}
              onClick={() => {
                // 点击图标：激活工作区 + 切换悬浮面板
                setActiveWorkspaceId(ws.id)
                setOpenWsId(openWsId === ws.id ? null : ws.id)
              }}
            />
          ))
        )}
      </div>

      {/* 分隔线 */}
      <div className="w-6 border-t border-gray-100 shrink-0" />

      {/* 新建工作区按钮 */}
      {creating ? (
        <div className="w-full px-1.5 shrink-0">
          <input
            ref={newWsInputRef}
            value={newWsName}
            onChange={(e) => setNewWsName(e.target.value)}
            onKeyDown={handleNewWsKey}
            onBlur={() => { if (!newWsName.trim()) setCreating(false) }}
            placeholder="名称"
            className="w-full text-[10px] bg-orange-50 border border-orange-200 rounded-lg px-1.5 py-1 outline-none text-gray-700 placeholder-orange-300 text-center"
          />
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          title="新建工作区"
          className="w-9 h-9 rounded-xl flex items-center justify-center border border-dashed border-gray-200 text-gray-300 hover:text-orange-400 hover:border-orange-300 hover:bg-orange-50 transition-all shrink-0"
        >
          <Icon path={ICONS.plus} className="w-4 h-4" />
        </button>
      )}

      {/* 错误提示（悬浮小角标）*/}
      {error && (
        <div className="absolute bottom-14 left-16 z-50 w-48 bg-red-50 border border-red-100 rounded-lg p-2 shadow-lg">
          <div className="flex items-start gap-1.5">
            <span className="text-red-400 text-[10px] flex-1 leading-relaxed">{error}</span>
            <button onClick={clearError} className="text-red-300 hover:text-red-500 shrink-0">
              <Icon path={ICONS.x} className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}

      {/* 悬浮工作区详情面板 */}
      {openWorkspace && (
        <WorkspacePanel
          workspace={openWorkspace}
          activeSessionId={activeSessionId}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
          onCreateSession={handleCreateSession}
          onRename={renameWorkspace}
          onDelete={handleDeleteWorkspace}
          onClose={() => setOpenWsId(null)}
          onRenameSession={handleRenameSession}
        />
      )}
    </div>
  )
}
