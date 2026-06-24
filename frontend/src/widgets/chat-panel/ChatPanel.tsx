'use client'

import {
  useCallback, useEffect, useRef, useState,
  type KeyboardEvent, type ClipboardEvent,
} from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/features/chat/store/chat.store'
import { useScoreStore } from '@/entities/session/store'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { chatUniversal } from '@/shared/lib/api'
import { ChatMessageList, StreamingAssistantCard } from './ChatMessageList'
import { TodoListCard } from './TodoListCard'
import { useBackendHealth, HEALTH_VISUAL } from '@/shared/hooks/useBackendHealth'
import { RoleSwitcher, RoleBadge } from '@/widgets/role-switcher'
import type { RoleMeta } from '@/widgets/role-switcher'

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const STICK_SLOP_PX = 80
const REQUEST_TIMEOUT_MS = 60_000

// ─── 附件类型 ─────────────────────────────────────────────────────────────────

type AttachmentKind = 'json' | 'midi' | 'audio' | 'text'

interface Attachment {
  kind: AttachmentKind
  name: string
  content: string   // 文本内容（text/json）或 base64（audio/midi）
  size: number      // 字节数
}

const KIND_ICON: Record<AttachmentKind, string> = {
  json:  '🎮',
  midi:  '🎹',
  audio: '🎵',
  text:  '📄',
}

const KIND_LABEL: Record<AttachmentKind, string> = {
  json:  'Sky JSON',
  midi:  'MIDI',
  audio: '音频',
  text:  '文本',
}

/** 根据文件名/内容判断附件类型 */
function detectKind(name: string, text: string): AttachmentKind {
  const lower = name.toLowerCase()
  if (lower.endsWith('.mid') || lower.endsWith('.midi')) return 'midi'
  if (lower.endsWith('.mp3') || lower.endsWith('.wav') || lower.endsWith('.m4a')) return 'audio'
  if (lower.endsWith('.json')) return 'json'
  // 无扩展名时尝试内容嗅探
  if (text.trimStart().startsWith('[') || text.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(text)
      const arr = Array.isArray(parsed) ? parsed : [parsed]
      if (arr[0]?.songNotes) return 'json'
    } catch { /* ignore */ }
    return 'json'
  }
  return 'text'
}

/** 格式化文件大小 */
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

// ─── AttachmentChip ───────────────────────────────────────────────────────────

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment
  onRemove: () => void
}) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 bg-orange-50 border border-orange-100 rounded-lg text-xs text-orange-700 max-w-[180px]">
      <span>{KIND_ICON[attachment.kind]}</span>
      <span className="truncate flex-1">{attachment.name}</span>
      <span className="text-orange-400 font-mono text-[10px] shrink-0">{fmtSize(attachment.size)}</span>
      <button
        onClick={onRemove}
        className="shrink-0 text-orange-300 hover:text-orange-600 transition-colors ml-0.5"
        aria-label="移除附件"
      >
        ✕
      </button>
    </div>
  )
}

// ─── SessionMenu（对话列表 Popover）────────────────────────────────────────────

function SessionMenu({
  open, onClose,
  sessions, activeSessionId,
  onSelect, onCreate, onDelete,
}: {
  open: boolean
  onClose: () => void
  sessions: { id: string; title: string | null }[]
  activeSessionId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
}) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    // rAF 延迟，避免打开时立即触发
    const id = requestAnimationFrame(() => document.addEventListener('mousedown', handler))
    return () => { cancelAnimationFrame(id); document.removeEventListener('mousedown', handler) }
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      ref={menuRef}
      className="absolute right-0 top-[calc(100%+4px)] z-[250] w-[min(260px,calc(100vw-2rem))] bg-white rounded-xl shadow-xl border border-gray-100 overflow-hidden"
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

// ─── ChatPanel ────────────────────────────────────────────────────────────────

/**
 * ChatPanel — 专业模式对话面板
 *
 * 架构要点：
 *   ① 统一调用 /chat 接口，LLM 自动识别意图（convert/edit/audio/voice/query）
 *   ② 支持粘贴附件（JSON/文本/MIDI），自动识别类型并作为 attachment 传给后端
 *   ③ 结束信号完全由 SSE 事件驱动（message.completed / abc.updated / error）
 *   ④ 超时兜底：REQUEST_TIMEOUT_MS 后若仍 running 则 failRun
 */
export function ChatPanel() {
  const router = useRouter()
  const { sessionId } = useScoreStore()
  const {
    activeSessionId,
    activeSessions,
    createSession,
    deleteSession,
    activeWorkspaceId,
    setActiveSessionId,
    setActiveWorkspaceId,
    workspaces,
  } = useWorkspaceStore()
  const {
    messages,
    streaming,
    status,
    currentStep,
    errorMessage,
    todos,
    todoSummary,
    todoDomain,
    activeRoleId,
    activeRoleName,
    activeRoleIcon,
    activeRoleColor,
    addOptimisticUserMessage,
    startRun,
    failRun,
    resetRuntime,
    setActiveRole,
  } = useChatStore()

  const [input, setInput] = useState('')
  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [showRolePanel, setShowRolePanel] = useState(false)
  const [showSessionMenu, setShowSessionMenu] = useState(false)

  // 当前活跃对话的标题
  const allSessions = activeSessions()
  const activeSession = allSessions.find((s) => s.id === activeSessionId)
  const sessionTitle = activeSession?.title || '新对话'

  // 新建对话
  const handleCreateSession = useCallback(async () => {
    if (!activeWorkspaceId) return
    try {
      const sess = await createSession(activeWorkspaceId, '新对话')
      router.push(`/pro/${sess.id}`)
    } catch { /* error 已在 store 中设置 */ }
  }, [activeWorkspaceId, createSession, router])

  // 切换对话
  const handleSelectSession = useCallback((id: string) => {
    if (id === activeSessionId) return
    // 找到 session 所属工作区
    for (const ws of workspaces) {
      if (ws.sessions?.some((s) => s.id === id)) {
        setActiveWorkspaceId(ws.id)
        break
      }
    }
    setActiveSessionId(id)
    router.push(`/pro/${id}`)
  }, [activeSessionId, workspaces, setActiveWorkspaceId, setActiveSessionId, router])

  // 删除对话
  const handleDeleteSession = useCallback(async (id: string) => {
    if (!window.confirm('确定删除该对话？消息记录将一并删除且不可恢复。')) return
    try {
      await deleteSession(id)
      // deleteSession 会设置 _pendingNavigateSessionId，由 page.tsx 监听跳转
    } catch { /* error 已在 store 中设置 */ }
  }, [deleteSession])

  // ── 角色恢复已统一由 /pro/[sessionId]/page.tsx 初始化 effect 调用 ─────────────
  // ChatPanel 不再重复调用 restoreRoleFromSession，避免 session 切换时双重 fetch。
  // 角色状态通过 useChatStore 订阅，page.tsx 调用后会自动触发 re-render。

  const scrollRef   = useRef<HTMLDivElement>(null)
  const stickRef    = useRef(true)
  const timeoutRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ── 自动滚底 ──────────────────────────────────────────────────────────────
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX
  }, [])

  useEffect(() => {
    if (!stickRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streaming.content, streaming.tool_calls.length])

  // ── 超时兜底清理 ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== 'running') {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
    }
  }, [status])

  // ── 粘贴附件处理 ──────────────────────────────────────────────────────────
  const handlePaste = useCallback(async (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const dt = e.clipboardData as DataTransfer
    const items = Array.from(dt.items) as DataTransferItem[]

    // 1. 优先处理文件粘贴
    const fileItem = items.find(
      (it: DataTransferItem) => it.kind === 'file' && (
        it.type.includes('json') ||
        it.type.includes('midi') ||
        it.type.includes('audio') ||
        it.type.includes('text') ||
        it.type === 'application/octet-stream'
      )
    )
    if (fileItem) {
      e.preventDefault()
      const file = (fileItem as DataTransferItem).getAsFile()
      if (!file) return

      const isAudio = file.type.includes('audio') || /\.(mp3|wav|m4a|ogg|flac)$/i.test(file.name)
      // MIDI 文件也需要 base64，H5Agent 解析 MIDI 依赖 b64 而非文本
      const isMidi = file.type.includes('midi') || /\.(mid|midi)$/i.test(file.name)
      if (isAudio || isMidi) {
        const reader = new FileReader()
        reader.onload = () => {
          const b64 = (reader.result as string).split(',')[1] ?? ''
          const kind: AttachmentKind = isAudio ? 'audio' : 'midi'
          setAttachment({ kind, name: file.name, content: b64, size: file.size })
        }
        reader.readAsDataURL(file)
      } else {
        const text = await file.text()
        const kind = detectKind(file.name, text)
        setAttachment({ kind, name: file.name, content: text, size: file.size })
      }
      return
    }

    // 2. 纯文本粘贴：检测是否像 Sky JSON（大段 JSON 作为附件）
    const textItem = items.find(
      (it: DataTransferItem) => it.kind === 'string' && it.type === 'text/plain'
    )
    if (textItem) {
      (textItem as DataTransferItem).getAsString((text: string) => {
        const trimmed = text.trim()
        if (trimmed.length > 200 && (trimmed.startsWith('[') || trimmed.startsWith('{'))) {
          e.preventDefault()
          const kind = detectKind('paste.json', trimmed)
          setAttachment({ kind, name: 'paste.json', content: trimmed, size: trimmed.length })
          setInput((prev) => prev || '帮我加载这首谱子')
        }
      })
    }
  }, [])

  // ── 发送消息 ──────────────────────────────────────────────────────────────
  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || status === 'running' || !sessionId) return

    // 构建用户可见的消息（含附件提示）
    const displayText = attachment
      ? `${text} [附件: ${attachment.name}]`
      : text

    addOptimisticUserMessage(displayText)
    setInput('')
    const att = attachment
    setAttachment(null)
    startRun()

    // 超时兜底
    timeoutRef.current = setTimeout(() => {
      failRun('请求超时，请检查后端连接')
    }, REQUEST_TIMEOUT_MS)

    // 统一调用 /chat 接口，LLM 自动识别意图
    // attachment_b64：音频 和 MIDI 均需要 b64（H5Agent 解析 MIDI 依赖 b64）
    // attachment_content：文本类附件（JSON/TXT/ABC）传文本内容
    try {
      await chatUniversal(sessionId, {
        message: text,
        attachment_content: att && att.kind !== 'audio' && att.kind !== 'midi' ? att.content : '',
        attachment_name:    att?.name ?? '',
        attachment_b64:     att && (att.kind === 'audio' || att.kind === 'midi') ? att.content : '',
      })
      // 结束信号来自 SSE: abc.updated / message.completed / error
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '请求失败，请检查后端服务'
      failRun(msg)
    }
  }, [input, attachment, status, sessionId, addOptimisticUserMessage, startRun, failRun])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isRunning        = status === 'running'
  const hasStreamContent = streaming.content || streaming.tool_calls.length > 0 || streaming.reasoning_content
  const backendHealth    = useBackendHealth()

  // 意图域配置（顶栏显示）
  const DOMAIN_LABEL: Record<string, { icon: string; label: string }> = {
    convert:        { icon: '🎮', label: '解析谱子' },
    edit:           { icon: '✏️', label: '编辑谱子' },
    create:         { icon: '🎵', label: '创作谱子' },
    audio:          { icon: '🎧', label: '生成音频' },
    voice:          { icon: '🎤', label: '音色克隆' },
    query:          { icon: '🔍', label: '查询分析' },
    'convert+edit': { icon: '🎮', label: '解析并编辑' },
    h5_create:      { icon: '🎨', label: 'H5 页面' },
    h5_edit:        { icon: '🖌️', label: 'H5 编辑' },
  }
  const domainInfo = todoDomain ? (DOMAIN_LABEL[todoDomain] ?? null) : null

  // 进度摘要文字（顶栏用）
  const runningTodo   = todos.find((t) => t.status === 'running')
  const topTodoCount  = todos.filter((t) => !t.parent_id).length
  const doneCount     = todos.filter((t) => t.status === 'done').length
  const hasTodos      = todos.length > 0

  // 根据当前状态/附件决定 placeholder
  const placeholder = !sessionId
    ? '请先创建 Session...'
    : isRunning
      ? `${currentStep ?? 'AI 处理中'}...`
      : attachment
        ? `描述对「${attachment.name}」的处理意图...`
        : '发消息或粘贴 JSON/音频文件，AI 自动识别意图...'

  return (
    <div className="flex flex-col h-full bg-white">

      {/* ── 顶栏 ── */}
      <div className="relative flex items-center gap-2 px-3 py-2 border-b border-gray-100 shrink-0 min-h-[40px]">
        {/* 左侧：对话切换 + 角色 + 状态 */}
        <div className="flex items-center gap-1 flex-1 min-w-0">
          {/* 对话标题（点击打开菜单） */}
          <button
            onClick={() => setShowSessionMenu((v) => !v)}
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
            onClick={() => void handleCreateSession()}
            title="新建对话"
            className="shrink-0 w-5 h-5 flex items-center justify-center rounded hover:bg-orange-50 text-gray-300 hover:text-orange-400 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
          </button>
          <span className="text-gray-100 shrink-0">|</span>

          {/* 角色：极简 icon+短名，点击切换 */}
          <button
            onClick={() => setShowRolePanel(true)}
            title={`切换角色：${activeRoleName}`}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-gray-50 transition-colors shrink-0"
          >
            <span className="text-sm leading-none">{activeRoleIcon}</span>
            <span className="text-[10px] text-gray-400 max-w-[52px] truncate">
              {activeRoleName.length > 5 ? activeRoleName.slice(0, 5) + '…' : activeRoleName}
            </span>
          </button>

          {/* 运行时：意图域图标 + x/y 进度，不展示文字 */}
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

          {/* 完成态：绿点 + 数字，不要文字 */}
          {!isRunning && hasTodos && doneCount === topTodoCount && topTodoCount > 0 && (
            <span className="flex items-center gap-1 shrink-0" title="全部完成">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
              <span className="text-[10px] text-green-400 tabular-nums">{doneCount}/{topTodoCount}</span>
            </span>
          )}
        </div>

        {/* 对话列表 Popover */}
        <SessionMenu
          open={showSessionMenu}
          onClose={() => setShowSessionMenu(false)}
          sessions={allSessions}
          activeSessionId={activeSessionId}
          onSelect={handleSelectSession}
          onCreate={() => void handleCreateSession()}
          onDelete={(id) => void handleDeleteSession(id)}
        />

        {/* 右侧：后端健康指示器（异常时才显示）+ 清空按钮 */}
        {HEALTH_VISUAL[backendHealth.status].show && (
          <span
            title={HEALTH_VISUAL[backendHealth.status].tip}
            className="shrink-0 flex items-center gap-1 text-[10px] font-medium"
          >
            <span className={[
              'w-2 h-2 rounded-full shrink-0',
              HEALTH_VISUAL[backendHealth.status].dot,
            ].join(' ')} />
            <span className="text-gray-400">{HEALTH_VISUAL[backendHealth.status].tip}</span>
          </span>
        )}
        {messages.length > 0 && !isRunning && (
          <button
            onClick={resetRuntime}
            className="shrink-0 text-xs text-gray-300 hover:text-gray-500 transition-colors"
            title="清空对话"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        )}
      </div>

      {/* ── TODO 规划卡片（固定在消息列表上方，不随消息流插入） ── */}
      {hasTodos && (
        <div className="px-3 pt-2 pb-0 shrink-0">
          <TodoListCard todos={todos} summary={todoSummary} domain={todoDomain || undefined} />
        </div>
      )}

      {/* ── 角色切换面板 ── */}
      {showRolePanel && sessionId && (
        <RoleSwitcher
          sessionId={sessionId}
          currentRoleId={activeRoleId}
          compact={false}
          onClose={() => setShowRolePanel(false)}
          onRoleChange={(role: RoleMeta, greeting: string) => {
            setShowRolePanel(false)
            // 切换后立即同步 store 角色状态（不等 SSE role.active 也能更新顶栏）
            setActiveRole(role.id, role.name, role.icon, role.color)
            // 将欢迎语直接注入对话框（修复：原来用 window.dispatchEvent 但无监听者）
            if (greeting) {
              useChatStore.getState().addGreetingMessage(greeting, role.name, role.icon)
            }
          }}
        />
      )}

      {/* ── 消息列表 ── */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto px-3 py-4 space-y-3"
      >
        {/* 空状态引导 */}
        {messages.length === 0 && !isRunning && (
          <div className="flex flex-col items-center justify-center h-full text-center py-8 space-y-3">
            <div className="w-14 h-14 bg-gradient-to-br from-orange-50 to-amber-50 rounded-2xl flex items-center justify-center shadow-sm">
              <span className="text-2xl">✨</span>
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-gray-700">告诉 AI 你想做什么</p>
              <p className="text-xs text-gray-400 max-w-[200px] leading-relaxed">
                直接说话，或粘贴 Sky JSON / 音频文件
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5 justify-center max-w-[240px]">
              {[
                '升高一个八度',
                '加快节奏',
                '生成中国风配乐',
                '克隆我的声音',
                '这首是什么调？',
              ].map((hint) => (
                <button
                  key={hint}
                  onClick={() => setInput(hint)}
                  className="text-xs px-2.5 py-1 bg-gray-50 hover:bg-orange-50 hover:text-orange-500 text-gray-500 rounded-lg transition-colors border border-gray-100 hover:border-orange-200"
                >
                  {hint}
                </button>
              ))}
            </div>
            {/* 粘贴提示 */}
            <p className="text-[10px] text-gray-300 flex items-center gap-1">
              <span>💡</span>
              <span>支持粘贴 Sky JSON / MP3 / MIDI 文件</span>
            </p>
          </div>
        )}

        <ChatMessageList messages={messages} />

        {/* 流式临时消息 */}
        {isRunning && hasStreamContent && (
          <StreamingAssistantCard
            content={streaming.content}
            reasoningContent={streaming.reasoning_content}
            toolCalls={streaming.tool_calls}
          />
        )}

        {/* 仅步骤提示，无流式内容 */}
        {isRunning && !hasStreamContent && currentStep && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-orange-50 text-xs text-orange-600">
            <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin shrink-0" />
            <span>{currentStep}</span>
          </div>
        )}
      </div>

      {/* ── 错误提示 ── */}
      {errorMessage && (
        <div className="mx-3 mb-2 px-3 py-2 bg-red-50 border border-red-100 rounded-xl text-xs text-red-600 flex items-start gap-2">
          <span className="shrink-0 mt-0.5">⚠️</span>
          <span className="flex-1">{errorMessage}</span>
          <button
            onClick={resetRuntime}
            className="shrink-0 text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── 输入区 ── */}
      <div className="px-3 pb-3 shrink-0 space-y-1.5">

        {/* 附件 chip */}
        {attachment && (
          <div className="flex items-center gap-2 px-1">
            <AttachmentChip
              attachment={attachment}
              onRemove={() => setAttachment(null)}
            />
            <span className="text-[10px] text-gray-400 flex items-center gap-1">
              <span>{KIND_ICON[attachment.kind]}</span>
              <span>{KIND_LABEL[attachment.kind]} 已就绪，发送时自动识别意图</span>
            </span>
          </div>
        )}

        <div className={[
          'flex items-end gap-2 rounded-xl border p-2 transition-all duration-200',
          isRunning
            ? 'border-gray-100 bg-gray-50 opacity-70'
            : 'border-gray-200 bg-white focus-within:border-orange-300 focus-within:shadow-sm focus-within:shadow-orange-50',
        ].join(' ')}>

          {/* 附件按钮（提示粘贴方式） */}
          <button
            className="shrink-0 w-6 h-6 flex items-center justify-center text-gray-300 hover:text-orange-400 transition-colors"
            title="粘贴 JSON / 音频文件到输入框即可附加"
            onClick={() => textareaRef.current?.focus()}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
            </svg>
          </button>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            disabled={isRunning || !sessionId}
            placeholder={placeholder}
            rows={1}
            style={{ minHeight: '32px', maxHeight: '120px' }}
            className={[
              'flex-1 text-sm resize-none bg-transparent outline-none leading-relaxed py-0.5',
              isRunning || !sessionId ? 'text-gray-300 cursor-not-allowed' : 'text-gray-700 placeholder:text-gray-300',
            ].join(' ')}
          />

          {/* 发送按钮 */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || isRunning || !sessionId}
            className={[
              'shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150',
              !input.trim() || isRunning || !sessionId
                ? 'bg-gray-100 text-gray-300 cursor-not-allowed'
                : 'bg-orange-500 text-white hover:bg-orange-600 active:scale-90 shadow-sm shadow-orange-200',
            ].join(' ')}
          >
            {isRunning ? (
              <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            )}
          </button>
        </div>

        <p className="text-[10px] text-gray-300 text-right pr-0.5">
          Enter 发送 · Shift+Enter 换行 · 粘贴文件自动识别
        </p>
      </div>
    </div>
  )
}
