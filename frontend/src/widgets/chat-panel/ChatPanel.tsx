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
import { RichInput, type FileRef } from './RichInput'
import { readWorkspaceFile, uploadFileToWorkspace } from '@/shared/lib/workspace-files-api'
import { useBackendHealth, HEALTH_VISUAL } from '@/shared/hooks/useBackendHealth'
import { RoleSwitcher, RoleBadge } from '@/widgets/role-switcher'
import type { RoleMeta } from '@/widgets/role-switcher'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const STICK_SLOP_PX = 80
// H5 生成等复杂任务需要 3-4 轮 LLM Tool Calling，每轮最长 180s，前端给足 5 分钟
const REQUEST_TIMEOUT_MS = 300_000

// ─── 附件类型 ─────────────────────────────────────────────────────────────────

type AttachmentKind = 'json' | 'midi' | 'audio' | 'text' | 'image'

interface Attachment {
  kind: AttachmentKind
  name: string
  content: string          // 文本内容（text/json/abc）；二进制文件留空
  workspace_path: string   // 工作区相对路径（二进制文件用此字段，不传 base64）
  size: number             // 字节数
}

const KIND_ICON: Record<AttachmentKind, string> = {
  json:  '🎮',
  midi:  '🎹',
  audio: '🎵',
  text:  '📄',
  image: '🖼️',
}

const KIND_LABEL: Record<AttachmentKind, string> = {
  json:  'Sky JSON',
  midi:  'MIDI',
  audio: '音频',
  text:  '文本',
  image: '图片',
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
    triggerFileTreeRefresh,
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

  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [imgUploadTip, setImgUploadTip] = useState<{ status: 'uploading' | 'done' | 'error'; name: string } | null>(null)
  // 插入 mention 节点的 ref（上传完成后自动把文件引用插入 tiptap）
  const insertMentionRef = useRef<((path: string, label: string, size: number) => void) | null>(null)
  const [showRolePanel, setShowRolePanel] = useState(false)
  const [showSessionMenu, setShowSessionMenu] = useState(false)
  // RichInput 的 insertText ref，用于快捷回复按钮向编辑器插入文本
  const insertTextRef = useRef<((text: string) => void) | null>(null)
  // RichInput 的 send ref，用于发送按钮直接触发（避免 DOM 事件不可靠问题）
  const richSendRef = useRef<(() => void) | null>(null)
  // RichInput 发送回调：文本文件读内容进 context，二进制文件只传工作区路径
  // 核心原则：图片/MIDI/音频的二进制内容永远不进 LLM context，防止上下文爆炸
  const handleRichSend = useCallback(async (text: string, fileRefs: FileRef[]) => {
    if (!text.trim() || status === 'running' || !sessionId) return
    let autoAtt: Attachment | null = attachment

    if (fileRefs.length > 0 && !autoAtt && activeWorkspaceId) {
      const ref = fileRefs[0]
      const ext = ref.label.split('.').pop()?.toLowerCase() ?? ''
      const isMidi  = ['mid', 'midi'].includes(ext)
      const isAudio = ['mp3', 'wav', 'm4a', 'ogg', 'flac'].includes(ext)
      const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)
      const isText  = ['abc', 'txt', 'md', 'json', 'html', 'csv'].includes(ext)

      try {
        if (isMidi || isAudio || isImage) {
          // ✅ 二进制文件：只传工作区路径，不读取 base64
          // 后端 Runner 层负责处理（MIDI → H5，图片 → visual_understanding URL）
          const kind: AttachmentKind = isMidi ? 'midi' : isAudio ? 'audio' : 'image'
          autoAtt = { kind, name: ref.label, content: '', workspace_path: ref.path, size: 0 }
        } else if (isText) {
          // ✅ 文本文件：读取内容（ABC/JSON/TXT 都是高效文本，可进 context）
          const content = await readWorkspaceFile(activeWorkspaceId, ref.path)
          const kind: AttachmentKind = ext === 'json' ? 'json' : 'text'
          autoAtt = { kind, name: ref.label, content, workspace_path: ref.path, size: content.length }
        }
      } catch { /* 静默失败，不阻塞发送 */ }
    }

    const displayText = (autoAtt && !text.includes(`[@${autoAtt.name}]`))
      ? `${text} [@${autoAtt.name}]`
      : text
    addOptimisticUserMessage(displayText)
    const att = autoAtt
    setAttachment(null)
    startRun()
    const timeoutId = setTimeout(() => failRun('请求超时，请检查后端连接'), REQUEST_TIMEOUT_MS)
    timeoutRef.current = timeoutId
    // 判断附件类型决定传哪个字段
    // - 有 workspace_path（已上传到工作区）：传路径，content/b64 留空
    // - 无 workspace_path 但有 content（降级 base64 路径）：
    //   - MIDI/音频 → 传 attachment_b64，让 Runner 层转存到工作区
    //   - 文本（ABC/JSON）→ 传 attachment_content，可进 LLM context
    const isBinary = att?.kind === 'midi' || att?.kind === 'audio'
    const hasWsPath = !!(att?.workspace_path)
    chatUniversal(sessionId, {
      // 使用 displayText（含 [@文件名] chip）而非原始 text，
      // 保证后端落库内容与前端乐观显示一致，刷新后 SSE replay 能正确还原 chip
      message: displayText,
      attachment_name: att?.name ?? '',
      // 工作区路径（优先）：后端 Runner 层直接使用
      attachment_workspace_path: att?.workspace_path ?? '',
      // 文本内容（ABC/JSON/TXT）：仅在非二进制且有内容时传
      attachment_content: (!isBinary && att?.content) ? att.content : '',
      // base64（降级路径）：仅在二进制且无 workspace_path 时传，Runner 层会转存到工作区
      attachment_b64: (isBinary && !hasWsPath && att?.content) ? att.content : '',
    }).catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : '请求失败'
      failRun(msg)
    })
  }, [status, sessionId, attachment, activeWorkspaceId, addOptimisticUserMessage, startRun, failRun])

  // 发送按钮：通过 RichInput 暴露的 sendRef 直接调用，避免 DOM 事件不可靠
  const handleSendButton = useCallback(() => {
    richSendRef.current?.()
  }, [])

  // 当前活跃对话的标题
  const allSessions = activeSessions()
  const activeSession = allSessions.find((s) => s.id === activeSessionId)
  const sessionTitle = activeSession?.title || '新对话'

  // 新建对话
  const handleCreateSession = useCallback(async () => {
    if (!activeWorkspaceId) return
    // ⚡ 预清空：在路由跳转前同步清空消息列表，消除跳转动画期间的旧消息残留
    // page.tsx 的 [sessionId] effect 也会清空，这里是双保险，确保视觉上立即生效
    useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
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

  // 删除对话（自制弹窗确认）
  const [deleteConfirmSessionId, setDeleteConfirmSessionId] = useState<string | null>(null)
  const handleDeleteSession = useCallback((id: string) => {
    setDeleteConfirmSessionId(id)
  }, [])
  const handleConfirmDeleteSession = useCallback(async () => {
    if (!deleteConfirmSessionId) return
    const id = deleteConfirmSessionId
    setDeleteConfirmSessionId(null)
    try {
      await deleteSession(id)
      // deleteSession 会设置 _pendingNavigateSessionId，由 page.tsx 监听跳转
    } catch { /* error 已在 store 中设置 */ }
  }, [deleteConfirmSessionId, deleteSession])

  // ── 角色恢复已统一由 /pro/[sessionId]/page.tsx 初始化 effect 调用 ─────────────
  // ChatPanel 不再重复调用 restoreRoleFromSession，避免 session 切换时双重 fetch。
  // 角色状态通过 useChatStore 订阅，page.tsx 调用后会自动触发 re-render。

  const scrollRef   = useRef<HTMLDivElement>(null)
  const stickRef    = useRef(true)
  const timeoutRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

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
      const isMidi  = file.type.includes('midi') || /\.(mid|midi)$/i.test(file.name)

      if ((isAudio || isMidi) && activeWorkspaceId) {
        // ✅ 新架构：MIDI/音频先上传到工作区，存 workspace_path，不存 base64
        // 这样 LLM context 中永远不会出现二进制内容，彻底避免超时
        const kind: AttachmentKind = isAudio ? 'audio' : 'midi'
        const subdir = isMidi ? '.sky' : 'shared'
        const destPath = `${subdir}/${file.name}`
        setImgUploadTip({ status: 'uploading', name: file.name })
        try {
          await uploadFileToWorkspace(activeWorkspaceId, file, destPath)
          setAttachment({ kind, name: file.name, content: '', workspace_path: destPath, size: file.size })
          setImgUploadTip({ status: 'done', name: file.name })
          // 上传成功：自动插入 @mention，不显示顶部胶囊
          insertMentionRef.current?.(destPath, file.name, file.size)
          triggerFileTreeRefresh()
        } catch {
          // 上传失败降级：仍然设置附件，但用 base64 路径（旧兼容路径）
          const reader = new FileReader()
          reader.onload = () => {
            const b64 = (reader.result as string).split(',')[1] ?? ''
            setAttachment({ kind, name: file.name, content: b64, workspace_path: '', size: file.size })
          }
          reader.readAsDataURL(file)
          setImgUploadTip({ status: 'error', name: file.name })
        }
      } else if (isAudio || isMidi) {
        // 无工作区时降级用 base64（兼容旧路径）
        const reader = new FileReader()
        reader.onload = () => {
          const b64 = (reader.result as string).split(',')[1] ?? ''
          const kind: AttachmentKind = isAudio ? 'audio' : 'midi'
          setAttachment({ kind, name: file.name, content: b64, workspace_path: '', size: file.size })
        }
        reader.readAsDataURL(file)
      } else {
        // txt / json 等文本文件：上传到工作区 + 插入 mention 胶囊
        const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
        const subdir = ext === 'json' ? 'shared' : 'shared'
        const destPath = `${subdir}/${file.name}`
        if (activeWorkspaceId) {
          setImgUploadTip({ status: 'uploading', name: file.name })
          try {
            await uploadFileToWorkspace(activeWorkspaceId, file, destPath)
            const text = await file.text()
            const kind = detectKind(file.name, text)
            setAttachment({ kind, name: file.name, content: text, workspace_path: destPath, size: file.size })
            setImgUploadTip({ status: 'done', name: file.name })
            insertMentionRef.current?.(destPath, file.name, file.size)
            triggerFileTreeRefresh()
          } catch {
            // 上传失败降级：读内容但无胶囊
            const text = await file.text()
            const kind = detectKind(file.name, text)
            setAttachment({ kind, name: file.name, content: text, workspace_path: '', size: file.size })
            setImgUploadTip({ status: 'error', name: file.name })
          }
        } else {
          const text = await file.text()
          const kind = detectKind(file.name, text)
          setAttachment({ kind, name: file.name, content: text, workspace_path: '', size: file.size })
        }
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
          // 提示用户可以输入意图（通过 insertTextRef 插入快捷文本）
          insertTextRef.current?.('帮我加载这首谱子')
        }
      })
    }
  }, [activeWorkspaceId, triggerFileTreeRefresh])

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

  // 图片上传状态自动消除
  useEffect(() => {
    if (!imgUploadTip || imgUploadTip.status === 'uploading') return
    const t = setTimeout(() => setImgUploadTip(null), 2500)
    return () => clearTimeout(t)
  }, [imgUploadTip])

  // 根据当前状态/附件决定 placeholder
  const placeholder = !sessionId
    ? '请先创建会话...'
    : isRunning
      ? `${currentStep ?? 'AI 处理中'}...`
      : attachment
        ? `描述对「${attachment.name}」的处理意图，或直接发送...`
        : '发消息 · 粘贴图片自动上传 · @ 引用文件'

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
            onClick={() => {
              // 同时清空消息列表和运行时状态，确保 UI 立即清空
              useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
            }}
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
                  onClick={() => insertTextRef.current?.(hint)}
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
            roundIdx={streaming.roundIdx}
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
      <div className="px-3 pb-3 shrink-0">
        <div className={[
          'rounded-2xl border transition-all duration-200 shadow-sm',
          isRunning
            ? 'border-gray-100 bg-gray-50/80 opacity-80'
            : 'border-gray-200 bg-white focus-within:border-orange-300 focus-within:shadow-md focus-within:shadow-orange-50/60',
        ].join(' ')}>

          {/* ── 顶部工具栏：专家角色（左）+ 上下文用量（右）── */}
          <div className="flex items-center justify-between px-3 pt-2.5 pb-1">
            {/* 左：专家角色选择按钮 */}
            <button
              onClick={() => setShowRolePanel(true)}
              disabled={isRunning}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors group disabled:opacity-50 disabled:cursor-not-allowed"
              title="切换专家角色"
            >
              <span className="text-base leading-none">{activeRoleIcon}</span>
              <span className="text-[11px] font-semibold text-gray-700 max-w-[80px] truncate group-hover:text-orange-600 transition-colors">
                {activeRoleName}
              </span>
              <svg className="w-3 h-3 text-gray-300 group-hover:text-orange-400 transition-colors shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* 右：上下文用量圆形指示器 */}
            {(() => {
              // 用消息数估算上下文占用（每条消息约 500 token，上限 128k）
              const estTokens = messages.reduce((acc, m) => acc + (m.content?.length ?? 0), 0)
              const pct = Math.min(99, Math.round(estTokens / 1280))
              const color = pct >= 80 ? 'text-red-500 bg-red-50 border-red-100'
                          : pct >= 50 ? 'text-orange-500 bg-orange-50 border-orange-100'
                          : 'text-gray-500 bg-gray-50 border-gray-100'
              return (
                <button
                  onClick={() => {
                    useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
                  }}
                  title={`上下文已用约 ${pct}%，点击清空对话`}
                  className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold transition-all hover:scale-105 ${color}`}
                >
                  <span className="tabular-nums">{pct}%</span>
                  <svg className="w-2.5 h-2.5 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                </button>
              )
            })()}
          </div>

          {/* ── Tiptap 富文本输入框 ── */}
          <div className="px-3 pb-1">
            <RichInput
              disabled={isRunning || !sessionId}
              placeholder={placeholder}
              onSend={handleRichSend}
              onPaste={handlePaste as unknown as (e: ClipboardEvent) => void}
              insertTextRef={insertTextRef}
              sendRef={richSendRef}
              insertMentionRef={insertMentionRef}
              onImageUploadStatus={(status, name) => {
                setImgUploadTip({ status, name: name ?? '' })
              }}
            />
          </div>

          {/* ── 底部工具栏 ── */}
          <div className="flex items-center justify-between px-2.5 pb-2.5 pt-1">

            {/* 左侧：模型选择 + 上传状态提示 */}
            <div className="flex items-center gap-1.5">
              {/* 模型选择按钮（占位，后续接真实模型列表） */}
              <button
                className="flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors group"
                title="选择模型"
              >
                <span className="w-4 h-4 rounded-md bg-gray-800 flex items-center justify-center shrink-0">
                  <span className="text-[7px] font-black text-white leading-none">EP</span>
                </span>
                <span className="text-[10px] text-gray-500 font-medium group-hover:text-gray-700 transition-colors hidden sm:inline">默认模型</span>
                <svg className="w-2.5 h-2.5 text-gray-300 group-hover:text-gray-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </button>

              {/* 上传状态提示 */}
              {imgUploadTip && (
                <span className={[
                  'text-[10px] flex items-center gap-1 transition-all',
                  imgUploadTip.status === 'uploading' ? 'text-orange-400' :
                  imgUploadTip.status === 'done'      ? 'text-green-500'  : 'text-red-400',
                ].join(' ')}>
                  {imgUploadTip.status === 'uploading' && (
                    <svg className="w-2.5 h-2.5 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  )}
                  {imgUploadTip.status === 'done'  && <span>✓</span>}
                  {imgUploadTip.status === 'error' && <span>✕</span>}
                  <span className="truncate max-w-[100px]">
                    {imgUploadTip.status === 'uploading' ? `上传中...` :
                     imgUploadTip.status === 'done'      ? `已引用` : `上传失败`}
                  </span>
                </span>
              )}
            </div>

            {/* 右侧：联网 + 语音 + 发送 */}
            <div className="flex items-center gap-1.5">
              {/* 联网模式按钮 */}
              <button
                className="w-7 h-7 rounded-lg flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-all"
                title="联网搜索（暂未开放）"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                    d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
                </svg>
              </button>

              {/* 语音输入按钮 */}
              <button
                className="w-7 h-7 rounded-lg flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-all"
                title="语音输入（暂未开放）"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
                    d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
              </button>

              {/* 发送按钮 */}
              <button
                onClick={handleSendButton}
                disabled={isRunning || !sessionId}
                className={[
                  'w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150',
                  isRunning || !sessionId
                    ? 'bg-gray-100 text-gray-300 cursor-not-allowed'
                    : 'bg-orange-500 text-white hover:bg-orange-600 active:scale-90 shadow-sm shadow-orange-200',
                ].join(' ')}
                title="发送（Enter）"
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
          </div>
        </div>
      </div>

      {/* 删除对话确认弹窗（自制，替代 window.confirm） */}
      {deleteConfirmSessionId && (() => {
        const sess = allSessions.find((s) => s.id === deleteConfirmSessionId)
        return (
          <ConfirmDialog
            title="删除对话"
            description={
              <>
                确定删除对话
                <span className="font-medium text-gray-700">「{sess?.title || '新对话'}」</span>
                吗？消息记录将一并删除且不可恢复。
              </>
            }
            confirmText="删除"
            variant="danger"
            onConfirm={handleConfirmDeleteSession}
            onCancel={() => setDeleteConfirmSessionId(null)}
          />
        )
      })()}
    </div>
  )
}
