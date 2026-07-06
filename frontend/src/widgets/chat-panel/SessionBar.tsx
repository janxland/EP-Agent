'use client'

/**
 * SessionBar — 对话列表栏
 *
 * 职责：
 *   - 显示当前对话标题（点击展开菜单）
 *   - 新建对话按钮
 *   - 对话列表 Popover（切换 / 删除）
 *   - 运行状态指示器（意图域图标 + 进度）
 *   - 角色徽章（点击切换角色）
 *
 * 与 ChatPanel 解耦：通过 props 接收所有数据和回调，无内部 store 依赖。
 */

import { useRef, useEffect } from 'react'
import type { DomainMeta } from '@/shared/constants/domain'

// ─── SessionMenu Popover ──────────────────────────────────────────────────────

interface SessionMenuProps {
  open: boolean
  onClose: () => void
  sessions: { id: string; title: string | null }[]
  activeSessionId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
}

function SessionMenu({
  open, onClose, sessions, activeSessionId, onSelect, onCreate, onDelete,
}: SessionMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  // 用 ref 稳定 onClose，避免 effect 因 onClose 引用变化重复注册
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose })

  useEffect(() => {
    if (!open) return
    // 用 capture=true 在捕获阶段拦截，避免冒泡竞态
    // 用 setTimeout 跳过触发打开菜单的那次 mousedown
    let timer: ReturnType<typeof setTimeout> | null = null
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onCloseRef.current()
      }
    }
    timer = setTimeout(() => {
      document.addEventListener('mousedown', handler)
    }, 0)
    return () => {
      if (timer !== null) clearTimeout(timer)
      document.removeEventListener('mousedown', handler)
    }
  }, [open])

  if (!open) return null

  return (
    <div
      ref={menuRef}
      className="absolute left-0 top-[calc(100%+4px)] z-[250] w-[min(260px,calc(100vw-2rem))] bg-white rounded-xl shadow-xl border border-gray-100 overflow-hidden"
    >
      <div className="px-3 py-2 border-b border-gray-100">
        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">切换对话</span>
      </div>
      <div className="max-h-56 overflow-y-auto">
        {sessions.length === 0 ? (
          <p className="px-3 py-3 text-xs text-gray-300 text-center">暂无对话</p>
        ) : (
          sessions.map((s) => {
            const isActive = s.id === activeSessionId
            return (
              <div key={s.id} className={[
                'flex items-center gap-1 px-2 py-1 mx-1 my-0.5 rounded-lg group',
                isActive ? 'bg-orange-50' : 'hover:bg-gray-50',
              ].join(' ')}>
                <button
                  onClick={() => { onSelect(s.id); onClose() }}
                  className={[
                    'flex-1 text-left text-xs truncate px-1 py-1 rounded transition-colors outline-none',
                    isActive ? 'font-semibold text-orange-700' : 'text-gray-600 hover:text-gray-900',
                  ].join(' ')}
                >
                  {s.title || '新对话'}
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(s.id) }}
                  title="删除对话"
                  className="shrink-0 w-6 h-6 flex items-center justify-center rounded text-gray-200 hover:text-red-400 hover:bg-red-50 opacity-0 group-hover:opacity-100 transition-all"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            )
          })
        )}
      </div>
      <div className="border-t border-gray-100 p-1.5">
        <button
          onClick={() => { onCreate(); onClose() }}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-orange-50 text-orange-400 hover:text-orange-500 transition-colors"
        >
          <svg className="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 4v16m8-8H4" />
          </svg>
          <span className="text-[11px] font-medium">新建对话</span>
        </button>
      </div>
    </div>
  )
}

// ─── SessionBar ───────────────────────────────────────────────────────────────

export interface SessionBarProps {
  // 对话状态
  sessionTitle: string
  sessions: { id: string; title: string | null }[]
  activeSessionId: string | null
  showSessionMenu: boolean
  onToggleSessionMenu: () => void
  onCloseSessionMenu: () => void
  onSelectSession: (id: string) => void
  onCreateSession: () => void
  onDeleteSession: (id: string) => void

  // 角色
  activeRoleIcon: string
  activeRoleName: string
  onOpenRolePanel: () => void

  // 运行状态
  isRunning: boolean
  domainInfo: DomainMeta | null
  hasTodos: boolean
  doneCount: number
  topTodoCount: number
}

export function SessionBar({
  sessionTitle, sessions, activeSessionId,
  showSessionMenu, onToggleSessionMenu, onCloseSessionMenu,
  onSelectSession, onCreateSession, onDeleteSession,
  activeRoleIcon, activeRoleName, onOpenRolePanel,
  isRunning, domainInfo, hasTodos, doneCount, topTodoCount,
}: SessionBarProps) {
  return (
    <div className="relative flex items-center gap-1 flex-1 min-w-0">
      {/* 对话标题（点击打开菜单） */}
      <button
        onClick={onToggleSessionMenu}
        title="切换对话"
        className="flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-orange-500 transition-colors max-w-[90px] shrink-0"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-orange-400 shrink-0" />
        <span className="truncate">{sessionTitle}</span>
        <svg className="w-2.5 h-2.5 text-gray-300 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* 新建对话 */}
      <button
        onClick={onCreateSession}
        title="新建对话"
        className="shrink-0 w-5 h-5 flex items-center justify-center rounded hover:bg-orange-50 text-gray-300 hover:text-orange-400 transition-colors"
      >
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
      </button>

      <span className="text-gray-100 shrink-0">|</span>

      {/* 角色徽章 */}
      <button
        onClick={onOpenRolePanel}
        title={`切换角色：${activeRoleName}`}
        className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-gray-50 transition-colors shrink-0"
      >
        <span className="text-sm leading-none">{activeRoleIcon}</span>
        <span className="text-[10px] text-gray-400 max-w-[52px] truncate">
          {activeRoleName.length > 5 ? activeRoleName.slice(0, 5) + '…' : activeRoleName}
        </span>
      </button>

      {/* 运行时：意图域图标 + 进度 */}
      {isRunning && (
        <span className="flex items-center gap-1 text-[10px] text-orange-400 shrink-0">
          {domainInfo && <span>{domainInfo.icon}</span>}
          <span className="w-2 h-2 border-[1.5px] border-orange-400 border-t-transparent rounded-full animate-spin" />
          {hasTodos
            ? <span className="tabular-nums">{doneCount}/{topTodoCount}</span>
            : <span>处理中</span>
          }
        </span>
      )}

      {/* 完成态：绿点 + 数字 */}
      {!isRunning && hasTodos && doneCount === topTodoCount && topTodoCount > 0 && (
        <span className="flex items-center gap-1 shrink-0" title="全部完成">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
          <span className="text-[10px] text-green-400 tabular-nums">{doneCount}/{topTodoCount}</span>
        </span>
      )}

      {/* 对话列表 Popover */}
      <SessionMenu
        open={showSessionMenu}
        onClose={onCloseSessionMenu}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={onSelectSession}
        onCreate={onCreateSession}
        onDelete={onDeleteSession}
      />
    </div>
  )
}
