'use client'

import { memo, useEffect, useRef, useState } from 'react'
import type { TodoItem } from '@/features/chat/store/chat.store'

// ─── 状态配置 ─────────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<
  TodoItem['status'],
  { icon: string; color: string; bg: string; border: string; label: string; ringColor: string }
> = {
  pending: {
    icon: '○',
    color: 'text-gray-400',
    bg: 'bg-white',
    border: 'border-gray-100',
    label: '待执行',
    ringColor: '',
  },
  running: {
    icon: '◎',
    color: 'text-orange-500',
    bg: 'bg-orange-50',
    border: 'border-orange-300',
    label: '执行中',
    ringColor: 'ring-2 ring-orange-200 ring-offset-1',
  },
  done: {
    icon: '●',
    color: 'text-green-500',
    bg: 'bg-green-50/60',
    border: 'border-green-100',
    label: '完成',
    ringColor: '',
  },
  failed: {
    icon: '✕',
    color: 'text-red-400',
    bg: 'bg-red-50',
    border: 'border-red-100',
    label: '失败',
    ringColor: '',
  },
  skipped: {
    icon: '○',
    color: 'text-gray-300',
    bg: 'bg-gray-50',
    border: 'border-gray-100 border-dashed',
    label: '跳过',
    ringColor: '',
  },
}

// ─── Domain 标签（图标 + 文字 + 颜色） ────────────────────────────────────────

const DOMAIN_CONFIG: Record<string, { icon: string; label: string; color: string; bg: string }> = {
  convert:        { icon: '🎮', label: '解析谱子',   color: 'text-blue-600',   bg: 'bg-blue-50'   },
  edit:           { icon: '✏️', label: '编辑谱子',   color: 'text-violet-600', bg: 'bg-violet-50' },
  create:         { icon: '🎵', label: '创作谱子',   color: 'text-pink-600',   bg: 'bg-pink-50'   },
  audio:          { icon: '🎧', label: '生成音频',   color: 'text-teal-600',   bg: 'bg-teal-50'   },
  voice:          { icon: '🎤', label: '音色克隆',   color: 'text-indigo-600', bg: 'bg-indigo-50' },
  query:          { icon: '🔍', label: '查询分析',   color: 'text-amber-600',  bg: 'bg-amber-50'  },
  'convert+edit': { icon: '🎮', label: '解析并编辑', color: 'text-blue-600',   bg: 'bg-blue-50'   },
}

// ─── 完成闪光动画（done 状态入场） ────────────────────────────────────────────

function useDoneFlash(status: TodoItem['status']) {
  const [flash, setFlash] = useState(false)
  const prevRef = useRef(status)
  useEffect(() => {
    if (prevRef.current !== 'done' && status === 'done') {
      setFlash(true)
      const t = setTimeout(() => setFlash(false), 900)
      return () => clearTimeout(t)
    }
    prevRef.current = status
  }, [status])
  return flash
}

// ─── 单条 TodoRow ──────────────────────────────────────────────────────────────

interface TodoRowProps {
  todo: TodoItem
  displayIndex: number
  isChild: boolean
  isNew: boolean
}

const TodoRow = memo(function TodoRow({ todo, displayIndex, isChild, isNew }: TodoRowProps) {
  const cfg = STATUS_CONFIG[todo.status]
  const flash = useDoneFlash(todo.status)
  const isRunning = todo.status === 'running'
  const isDone = todo.status === 'done'
  const isSkipped = todo.status === 'skipped'

  return (
    <div
      className={[
        'relative flex items-start gap-2.5 px-3 py-2 rounded-xl border transition-all duration-300',
        cfg.bg,
        cfg.border,
        cfg.ringColor,
        isChild ? 'ml-5 scale-[0.97]' : '',
        isNew ? 'animate-[fadeSlideIn_0.35s_ease-out]' : '',
        flash ? 'animate-[doneFlash_0.6s_ease-out]' : '',
        isRunning ? 'shadow-sm shadow-orange-100' : '',
      ].join(' ')}
      style={isNew ? { animationFillMode: 'both' } : undefined}
    >
      {/* 子任务连接线 */}
      {isChild && (
        <span className="absolute -left-3 top-1/2 w-3 h-px bg-orange-200 pointer-events-none" aria-hidden />
      )}

      {/* 状态图标 */}
      <div className="shrink-0 mt-0.5 w-4 flex items-center justify-center">
        {isRunning ? (
          <span className="inline-block w-3.5 h-3.5 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
        ) : isDone ? (
          <span className="text-green-500 text-xs font-bold">✓</span>
        ) : isSkipped ? (
          <span className="text-gray-300 text-xs">○</span>
        ) : (
          <span className={['text-xs font-bold', cfg.color].join(' ')}>{cfg.icon}</span>
        )}
      </div>

      {/* 内容 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          {isChild && (
            <span className="text-[9px] px-1 py-0.5 bg-orange-100 text-orange-500 rounded font-semibold shrink-0">
              ↳ 子步骤
            </span>
          )}

          {/* 序号 + 标题 */}
          <span className={[
            'text-[11px] font-semibold leading-snug',
            isDone    ? 'text-gray-400 line-through' :
            isSkipped ? 'text-gray-300 line-through' :
            isRunning ? 'text-orange-700' : 'text-gray-700',
          ].join(' ')}>
            {isChild ? '•' : `${displayIndex}.`} {todo.title}
          </span>

          {/* 状态 badge */}
          <span className={[
            'text-[9px] px-1.5 py-0.5 rounded-full font-semibold shrink-0',
            isRunning ? 'bg-orange-100 text-orange-600' :
            isDone    ? 'bg-green-100 text-green-600'   :
            isSkipped ? 'bg-gray-100 text-gray-300'     :
            todo.status === 'failed' ? 'bg-red-100 text-red-500' :
            'bg-gray-100 text-gray-400',
          ].join(' ')}>
            {cfg.label}
          </span>
        </div>

        {/* 详情文字 */}
        {todo.detail && (
          <p className={[
            'text-[10px] mt-0.5 leading-relaxed',
            isRunning ? 'text-orange-500' : 'text-gray-400',
          ].join(' ')}>
            {todo.detail}
          </p>
        )}

        {/* running 时显示「正在处理…」动态提示 */}
        {isRunning && (
          <p className="text-[10px] text-orange-400 mt-0.5 flex items-center gap-1">
            <span className="inline-block w-1 h-1 rounded-full bg-orange-400 animate-bounce" style={{ animationDelay: '0ms' }} />
            <span className="inline-block w-1 h-1 rounded-full bg-orange-400 animate-bounce" style={{ animationDelay: '150ms' }} />
            <span className="inline-block w-1 h-1 rounded-full bg-orange-400 animate-bounce" style={{ animationDelay: '300ms' }} />
            <span className="ml-0.5">正在处理…</span>
          </p>
        )}
      </div>
    </div>
  )
})

// ─── TodoListCard ─────────────────────────────────────────────────────────────

interface TodoListCardProps {
  todos: TodoItem[]
  summary?: string
  domain?: string
}

export const TodoListCard = memo(function TodoListCard({
  todos,
  summary,
  domain,
}: TodoListCardProps) {
  if (!todos.length) return null

  // ── 展开/收起状态：出现时展开，2s 无交互自动收起 ──────────────────────────────
  const [expanded, setExpanded] = useState(true)
  const autoCollapseRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)

  // 首次出现：2s 后自动收起
  useEffect(() => {
    autoCollapseRef.current = setTimeout(() => setExpanded(false), 2000)
    return () => { if (autoCollapseRef.current) clearTimeout(autoCollapseRef.current) }
  }, [])

  // 鼠标进入：取消自动收起
  const handleMouseEnter = () => {
    if (autoCollapseRef.current) {
      clearTimeout(autoCollapseRef.current)
      autoCollapseRef.current = null
    }
  }

  // 鼠标离开：若仍展开，1.5s 后收起
  const handleMouseLeave = () => {
    if (expanded) {
      autoCollapseRef.current = setTimeout(() => setExpanded(false), 1500)
    }
  }

  // 顶层 / 子任务分类
  const topTodos = todos.filter((t) => !t.parent_id)
  const childMap = todos.reduce<Record<string, TodoItem[]>>((acc, t) => {
    if (t.parent_id) {
      acc[t.parent_id] = acc[t.parent_id] ?? []
      acc[t.parent_id].push(t)
    }
    return acc
  }, {})

  // 统计
  const total        = todos.length
  const doneCount    = todos.filter((t) => t.status === 'done').length
  const skippedCount = todos.filter((t) => t.status === 'skipped').length
  const runningCount = todos.filter((t) => t.status === 'running').length
  const failedCount  = todos.filter((t) => t.status === 'failed').length
  const progress     = total > 0 ? Math.round((doneCount / total) * 100) : 0
  const hasDerived   = todos.some((t) => !!t.parent_id)

  // 当前正在执行的步骤（用于进度摘要文字）
  const runningTodo  = todos.find((t) => t.status === 'running')

  // Domain 配置
  const domainCfg = domain ? (DOMAIN_CONFIG[domain] ?? null) : null

  // 整体完成状态
  const allDone    = (doneCount + skippedCount) === total && total > 0 && doneCount > 0
  const hasFailed  = failedCount > 0
  const hasSkipped = skippedCount > 0

  return (
    <div
      ref={cardRef}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={[
        'rounded-2xl border overflow-hidden shadow-sm transition-all duration-300',
        hasFailed  ? 'border-red-100 bg-gradient-to-br from-red-50/40 to-orange-50/20' :
        allDone    ? 'border-green-100 bg-gradient-to-br from-green-50/50 to-emerald-50/30' :
                     'border-orange-100 bg-gradient-to-br from-orange-50/60 to-amber-50/40',
      ].join(' ')}
    >

      {/* ── 头部：点击展开/收起 ── */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3.5 py-2.5 text-left"
      >
        {/* 第一行：图标 + 意图域标签 + 动态拆分标记 + 进度数字 + 展开箭头 */}
        <div className="flex items-center gap-2">
          <span className="text-sm shrink-0">{allDone ? '✅' : hasFailed ? '⚠️' : '📋'}</span>

          <div className="flex-1 flex items-center gap-1.5 flex-wrap min-w-0">
            <span className="text-xs font-semibold text-gray-700 shrink-0">任务规划</span>

            {/* 意图域 badge */}
            {domainCfg && (
              <span className={[
                'text-[10px] px-2 py-0.5 rounded-full font-semibold shrink-0 flex items-center gap-1',
                domainCfg.color, domainCfg.bg,
              ].join(' ')}>
                <span>{domainCfg.icon}</span>
                <span>{domainCfg.label}</span>
              </span>
            )}

            {/* 动态拆分标记 */}
            {hasDerived && (
              <span className="text-[9px] px-1.5 py-0.5 bg-amber-100 text-amber-600 rounded-full font-semibold flex items-center gap-0.5 shrink-0">
                <span className="w-1 h-1 rounded-full bg-amber-500 animate-pulse inline-block" />
                动态拆分
              </span>
            )}
          </div>

          {/* 进度数字 */}
          <div className="shrink-0 flex items-center gap-2">
            <div className="flex flex-col items-end gap-0.5">
              <span className={[
                'text-xs font-bold tabular-nums',
                allDone ? 'text-green-500' : hasFailed ? 'text-red-400' : 'text-orange-500',
              ].join(' ')}>
                {doneCount}/{total}
              </span>
              {runningCount > 0 && (
                <span className="text-[9px] text-orange-400 flex items-center gap-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-orange-400 animate-pulse" />
                  进行中
                </span>
              )}
              {allDone && (
                <span className="text-[9px] text-green-500 font-semibold">已完成</span>
              )}
            </div>
            {/* 展开/收起箭头 + 文字提示 */}
            <div className={[
              'flex items-center gap-1 px-1.5 py-0.5 rounded-md transition-all duration-200 shrink-0',
              expanded ? 'bg-orange-100 text-orange-500' : 'bg-gray-100 text-gray-500 hover:bg-orange-50 hover:text-orange-400',
            ].join(' ')}>
              <span className="text-[9px] font-medium">{expanded ? '收起' : '展开'}</span>
              <svg
                className={['w-3 h-3 transition-transform duration-200', expanded ? 'rotate-180' : ''].join(' ')}
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
        </div>

        {/* 第二行：摘要（始终可见） */}
        <div className="mt-1 min-h-[16px]">
          {runningTodo ? (
            <p className="text-[11px] text-orange-600 font-medium flex items-center gap-1 leading-snug">
              <span className="shrink-0 inline-block w-2.5 h-2.5 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
              <span className="truncate">
                正在执行：<span className="font-semibold">{runningTodo.title}</span>
                <span className="text-orange-400 ml-1">（{topTodos.findIndex((t) => t.id === runningTodo.id) + 1}/{topTodos.length}）</span>
              </span>
            </p>
          ) : allDone ? (
            <p className="text-[11px] text-green-600 font-medium flex items-center gap-1">
              <span>🎉</span>
              <span className="truncate">所有步骤已完成{summary ? `：${summary}` : ''}</span>
            </p>
          ) : summary ? (
            <p className="text-[11px] text-gray-500 truncate leading-snug">{summary}</p>
          ) : null}
        </div>
      </button>

      {/* ── 可折叠内容区 ── */}
      <div className={['transition-all duration-300 overflow-hidden', expanded ? 'max-h-[600px] opacity-100' : 'max-h-0 opacity-0'].join(' ')}>
        {/* 进度条 */}
        <div className="h-1 bg-orange-100/80">
          <div
            className={[
              'h-full transition-all duration-700 ease-out',
              allDone   ? 'bg-gradient-to-r from-green-400 to-emerald-400' :
              hasFailed ? 'bg-gradient-to-r from-red-300 to-orange-300' :
                          'bg-gradient-to-r from-orange-400 to-amber-400',
            ].join(' ')}
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* TODO 列表 */}
        <div className="px-3 py-2.5 space-y-1.5">
          {topTodos.map((todo, idx) => (
            <div key={todo.id} className="relative">
              <TodoRow todo={todo} displayIndex={idx + 1} isChild={false} isNew={false} />
              {childMap[todo.id] && (
                <div className="mt-1 space-y-1 relative">
                  <div className="absolute left-5 top-0 bottom-0 w-px bg-orange-200" aria-hidden />
                  {childMap[todo.id].map((child) => (
                    <TodoRow key={child.id} todo={child} displayIndex={0} isChild={true} isNew={true} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* 底部失败提示 */}
        {hasFailed && (
          <div className="px-3.5 pb-2.5">
            <p className="text-[10px] text-red-400 flex items-center gap-1">
              <span>⚠️</span>
              <span>{failedCount} 个步骤失败，请查看详情或重新发起</span>
            </p>
          </div>
        )}
        {hasSkipped && !hasFailed && (
          <div className="px-3.5 pb-2.5">
            <p className="text-[10px] text-gray-400 flex items-center gap-1">
              <span>○</span>
              <span>{skippedCount} 个步骤被跳过（未执行即结束）</span>
            </p>
          </div>
        )}
      </div>
    </div>
  )
})
