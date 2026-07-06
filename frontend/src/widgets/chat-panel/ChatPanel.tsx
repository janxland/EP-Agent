'use client'

/**
 * ChatPanel — 专业模式对话面板（重构版）
 *
 * 架构原则（低耦合）：
 *   - ChatPanel 只做"组合"，不含业务逻辑
 *   - 业务逻辑下沉到专职 hook：useSendMessage / useAttachment / useContextUsage
 *   - UI 拆分为独立组件：SessionBar / InputBox / ModelPicker
 *   - DOMAIN_LABEL / ROLE_HINTS 提取到 shared/constants/domain.ts
 *   - store 直接写操作统一通过 clearMessages action，不再散落各处
 *
 * 职责边界：
 *   ✅ 组合 SessionBar + 消息列表 + InputBox + RoleSwitcher + ConfirmDialog
 *   ✅ 管理 UI 状态（showRolePanel / showSessionMenu / showModelMenu）
 *   ✅ 路由跳转（新建/切换对话）
 *   ❌ 不含附件处理细节（→ useAttachment）
 *   ❌ 不含发送/超时/中断细节（→ useSendMessage）
 *   ❌ 不含上下文用量轮询（→ useContextUsage）
 *   ❌ 不含模型列表加载（→ 本地 state + useEffect，已足够简单）
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useChatStore } from '@/features/chat/store/chat.store'
import { useScoreStore } from '@/entities/session/store'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import {
  listModels, setActiveModel, setSessionRole, type ModelItem,
  subscribeToSession, getSessionMessages, getSessionTodos,
} from '@/shared/lib/api'
import type { ToolCall } from '@/features/chat/types/chat.types'
import { useBackendHealth, HEALTH_VISUAL } from '@/shared/hooks/useBackendHealth'
import { ChatMessageList, StreamingAssistantCard } from './ChatMessageList'
import { TodoListCard } from './TodoListCard'
import { SessionBar } from './SessionBar'
import { InputBox } from './InputBox'
import { useAttachment } from './hooks/useAttachment'
import { useSendMessage } from './hooks/useSendMessage'
import { useContextUsage } from './hooks/useContextUsage'
import { RoleSwitcher, RoleBadge } from '@/widgets/role-switcher'
import type { RoleMeta } from '@/widgets/role-switcher'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { getDomainMeta, getRoleHints } from '@/shared/constants/domain'
import { PipelineTimelineButton, PipelineTimeline } from '@/widgets/pipeline-status/PipelineTimeline'

const STICK_SLOP_PX = 80

export function ChatPanel() {
  const router = useRouter()
  const { sessionId } = useScoreStore()

  const {
    activeSessionId,
    activeSessions,
    createSession,
    deleteSession,
    activeWorkspaceId,
    activeProjectId,
    setActiveSessionId,
    setActiveWorkspaceId,
    workspaces,
    triggerFileTreeRefresh,
    activeProject,
  } = useWorkspaceStore()

  const resolvedProjectId   = activeProjectId  ?? activeProject()?.id ?? ''
  const resolvedWorkspaceId = activeWorkspaceId ?? ''

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
    resetRuntime,
    setActiveRole,
    clearMessages,
  } = useChatStore()

  // ── UI 状态 ──────────────────────────────────────────────────────────────
  const [showRolePanel,    setShowRolePanel]    = useState(false)
  const [showSessionMenu,  setShowSessionMenu]  = useState(false)
  const [showModelMenu,    setShowModelMenu]    = useState(false)
  const [deleteConfirmId,  setDeleteConfirmId]  = useState<string | null>(null)

  // ── 模型列表 ─────────────────────────────────────────────────────────────
  const [models,        setModels]        = useState<ModelItem[]>([])
  const [activeModelId, setActiveModelId] = useState('')

  useEffect(() => {
    listModels()
      .then(({ models: list, active }) => { setModels(list); setActiveModelId(active) })
      .catch(() => {})
  }, [])

  // ── Session 切换：原子初始化（下沉自 page.tsx，实现无刷新切换）────────────────
  // 监听 activeSessionId 变化，只重载消息区域，其他组件（文件树/预览/顶栏）完全不感知
  const unsubRef = useRef<(() => void) | null>(null)
  const { setSessionId, handleSSEEvent: scoreHandleSSE, reset: resetScore } = useScoreStore()
  const { handleSSEEvent: chatHandleSSE, setTodos, restoreRoleFromSession } = useChatStore(
    (s) => ({
      handleSSEEvent:         s.handleSSEEvent,
      setTodos:               s.setTodos,
      restoreRoleFromSession: s.restoreRoleFromSession,
    })
  )
  const { restoreFromSessionId } = useWorkspaceStore()

  useEffect(() => {
    if (!activeSessionId) return

    // 1. 断开旧 SSE
    unsubRef.current?.()
    unsubRef.current = null

    // 2. 原子清空（顺序：先 score，再 chat runtime，再 messages）
    resetScore()
    resetRuntime()
    useChatStore.setState({ messages: [], todos: [], todoSummary: '', todoDomain: '', _roleRestored: false })

    // 3. 写入 sessionId（scoreStore 依赖此值）
    setSessionId(activeSessionId)

    // 4. 补全 workspaceId（本地有则同步，无则异步拉取）
    void restoreFromSessionId(activeSessionId)

    // 5. 恢复角色
    restoreRoleFromSession(activeSessionId)

    // 6. 立即建立 SSE（不等历史加载，避免事件丢失）
    unsubRef.current = subscribeToSession(activeSessionId, (event) => {
      scoreHandleSSE(event)
      chatHandleSSE(event)
    })

    // 7. 并行拉取历史消息 + TODO
    let cancelled = false
    ;(async () => {
      try {
        const [msgResult, todoResult] = await Promise.allSettled([
          getSessionMessages(activeSessionId),
          getSessionTodos(activeSessionId),
        ])
        if (cancelled) return

        if (msgResult.status === 'fulfilled' && msgResult.value.messages?.length > 0) {
          const rawMsgs = msgResult.value.messages

          // ── 构建 tool message 索引（供 assistant 关联）──────────────────────
          // 后端 role=tool 消息需要单独保留，供 ChatMessageList 渲染工具结果卡片
          const chatMsgs = rawMsgs
            .filter((m) => m.role === 'user' || m.role === 'assistant' || m.role === 'tool')
            // RENDER-3: tool 消息必须有 tool_call_id 才能关联工具卡片；空 id 的孤立 tool 消息跳过
            // RENDER-4: assistant 消息允许 content 为空（纯工具调用轮次），但必须有 tool_calls
            .filter((m) => {
              if (m.role === 'tool') return !!(m.tool_call_id?.trim())
              if (m.role === 'assistant') return !!(m.content?.trim()) || !!(m.tool_calls)
              return !!(m.content?.trim()) // user
            })
            .map((m) => {
              if (m.role === 'tool') {
                return {
                  id:           m.id,
                  role:         'tool' as const,
                  content:      m.content ?? '',
                  tool_call_id: m.tool_call_id ?? '',
                  name:         m.tool_name ?? '',
                  createdAt:    m.created_at,
                }
              }
              if (m.role === 'assistant') {
                // RENDER-2: tool_calls 后端存储为 JSON 字符串，解析后断言为 ToolCall[]
                // 类型断言确保 tc.function.name 等字段访问不报 TypeScript 错误
                let toolCalls: ToolCall[] | undefined
                if (m.tool_calls) {
                  if (typeof m.tool_calls === 'string') {
                    try { toolCalls = JSON.parse(m.tool_calls) as ToolCall[] } catch { toolCalls = undefined }
                  } else if (Array.isArray(m.tool_calls)) {
                    toolCalls = m.tool_calls as ToolCall[]
                  }
                }
                return {
                  id:         m.id,
                  role:       'assistant' as const,
                  content:    m.content ?? '',
                  createdAt:  m.created_at,
                  kind:       'turn' as const,
                  ...(toolCalls?.length ? { tool_calls: toolCalls } : {}),
                  ...(m.reasoning ? { reasoning_content: m.reasoning } : {}),
                }
              }
              // user
              return {
                id:        m.id,
                role:      'user' as const,
                content:   m.content ?? '',
                createdAt: m.created_at,
              }
            })
          if (chatMsgs.length > 0 && !cancelled) {
            // RENDER-1: 用 setMessages() 而非直接 setState，触发合并去重逻辑，
            // 防止 HTTP 覆盖 SSE replay 已追加的消息（竞态场景）
            useChatStore.getState().setMessages(chatMsgs)
          }
        }

        if (todoResult.status === 'fulfilled' && todoResult.value.todos?.length > 0) {
          const rawTodos = todoResult.value.todos
          const todoItems = rawTodos.map((t) => ({
            id:     t.id,
            title:  t.title,
            detail: t.detail,
            status: (t.status as 'pending' | 'running' | 'done' | 'failed' | 'skipped') ?? 'done',
          }))
          if (!cancelled) setTodos(todoItems, rawTodos[0]?.summary ?? '', rawTodos[0]?.domain ?? '')
        }
      } catch (err) {
        if (!cancelled) console.warn('[ChatPanel] 历史恢复失败:', err)
      }
    })()

    return () => {
      cancelled = true
      unsubRef.current?.()
      unsubRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId])

  const handleSelectModel = useCallback(async (id: string) => {
    setActiveModelId(id)
    try { await setActiveModel(id) } catch {}
  }, [])

  // ── 专职 hooks ───────────────────────────────────────────────────────────
  const insertTextRef    = useRef<((text: string) => void) | null>(null)
  const richSendRef      = useRef<(() => void) | null>(null)
  const insertMentionRef = useRef<((path: string, label: string, size: number) => void) | null>(null)

  const {
    attachment, clearAttachment, uploadTip,
    handlePaste, createFromFileRef,
    setAttachment,
  } = useAttachment({
    activeWorkspaceId,
    resolvedProjectId,
    triggerFileTreeRefresh,
    insertText:    (t) => insertTextRef.current?.(t),
    insertMention: (path, label, size) => insertMentionRef.current?.(path, label, size),
  })

  const { handleSend, handleAbort, isRunning } = useSendMessage({
    sessionId,
    resolvedWorkspaceId,
    resolvedProjectId,
  })

  const ctxPct = useContextUsage({ sessionId, status, messages })

  // ── 自动滚底 ─────────────────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef  = useRef(true)

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX
  }, [])

  useEffect(() => {
    if (!stickRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streaming.content, streaming.tool_calls.length])

  // ── RichInput 发送回调（附件组装在这里）─────────────────────────────────
  const handleRichSend = useCallback(async (text: string, fileRefs: import('./RichInput').FileRef[]) => {
    let att = attachment
    if (fileRefs.length > 0 && !att) {
      att = await createFromFileRef(fileRefs[0].path, fileRefs[0].label, fileRefs[0].size) ?? null
      if (att) setAttachment(att)
    }
    await handleSend(text, att)
    clearAttachment()
  }, [attachment, createFromFileRef, setAttachment, handleSend, clearAttachment])

  // ── 对话管理 ─────────────────────────────────────────────────────────────
  const allSessions  = activeSessions()
  const sessionTitle = allSessions.find((s) => s.id === activeSessionId)?.title || '新对话'

  const handleCreateSession = useCallback(async () => {
    if (!activeWorkspaceId) return
    // 不在这里 clearMessages()：路由跳转后 page.tsx 的 sessionId effect 会原子清空
    // 提前清空反而导致当前对话消息闪烁消失，体验差
    try {
      const sess = await createSession(activeWorkspaceId, '新对话', resolvedProjectId || undefined)
      const pid  = resolvedProjectId || useWorkspaceStore.getState().activeProject()?.id || ''
      // 继承当前角色：新建会话自动沿用当前专家，无需手动切换
      const currentRoleId = useChatStore.getState().activeRoleId
      if (currentRoleId && currentRoleId !== 'abc_expert') {
        try {
          await setSessionRole(sess.id, currentRoleId)
        } catch {
          // 静默失败：角色继承失败不影响新建会话主流程
        }
      }
      // 同项目内新建话题：用 replace 而非 push，避免产生多余浏览器历史
      // 且 replace 在 Next.js App Router 中不会触发整页重挂载（仅更新 params），
      // 从而避免 page.tsx 的 setActiveProjectId effect 重跑导致文件树刷新
      router.replace(`/pro/${pid}/${sess.id}`)
    } catch {}
  }, [activeWorkspaceId, resolvedProjectId, createSession, router])

  const handleSelectSession = useCallback((id: string) => {
    if (id === activeSessionId) return
    // 从工作区树中找到 session 所属的 wsId 和 projId（同时更新 store）
    const state = useWorkspaceStore.getState()
    let foundWsId = ''
    let foundProjId = ''
    for (const ws of state.workspaces) {
      for (const proj of ws.projects ?? []) {
        if (proj.sessions?.some((s) => s.id === id)) {
          foundWsId  = ws.id
          foundProjId = proj.id
          break
        }
      }
      if (!foundWsId && ws.sessions?.some((s) => s.id === id)) {
        foundWsId = ws.id
      }
      if (foundWsId) break
    }
    if (foundWsId) setActiveWorkspaceId(foundWsId)
    setActiveSessionId(id)
    const pid = foundProjId || resolvedProjectId || ''
    // 同项目内切换 session 用 replace 而非 push，避免堆积浏览器历史（Back 键行为异常）
    const isSameProject = !foundProjId || foundProjId === resolvedProjectId
    if (isSameProject) {
      router.replace(`/pro/${pid}/${id}`)
    } else {
      router.push(`/pro/${pid}/${id}`)
    }
  }, [activeSessionId, setActiveWorkspaceId, setActiveSessionId, router, resolvedProjectId])

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteConfirmId) return
    const id = deleteConfirmId
    setDeleteConfirmId(null)
    try { await deleteSession(id) } catch {}
  }, [deleteConfirmId, deleteSession])

  // ── 派生状态 ─────────────────────────────────────────────────────────────
  const domainInfo     = getDomainMeta(todoDomain)
  const roleHints      = getRoleHints(activeRoleId)
  const hasTodos       = todos.length > 0
  const topTodoCount   = todos.filter((t) => !t.parent_id).length
  const doneCount      = todos.filter((t) => t.status === 'done').length
  const hasStreamContent = streaming.content || streaming.tool_calls.length > 0 || streaming.reasoning_content
  const backendHealth  = useBackendHealth()

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
        <SessionBar
          sessionTitle={sessionTitle}
          sessions={allSessions}
          activeSessionId={activeSessionId}
          showSessionMenu={showSessionMenu}
          onToggleSessionMenu={() => setShowSessionMenu((v) => !v)}
          onCloseSessionMenu={() => setShowSessionMenu(false)}
          onSelectSession={handleSelectSession}
          onCreateSession={() => void handleCreateSession()}
          onDeleteSession={(id) => setDeleteConfirmId(id)}
          activeRoleIcon={activeRoleIcon}
          activeRoleName={activeRoleName}
          onOpenRolePanel={() => setShowRolePanel(true)}
          isRunning={isRunning}
          domainInfo={domainInfo}
          hasTodos={hasTodos}
          doneCount={doneCount}
          topTodoCount={topTodoCount}
        />

        {/* 右侧：链路审计 + 后端健康 + 清空按钮 */}
        <div className="flex items-center gap-2 ml-auto shrink-0">
          {sessionId && <PipelineTimelineButton sessionId={sessionId} />}
          {HEALTH_VISUAL[backendHealth.status].show && (
            <span
              title={HEALTH_VISUAL[backendHealth.status].tip}
              className="flex items-center gap-1 text-[10px] font-medium"
            >
              <span className={['w-2 h-2 rounded-full shrink-0', HEALTH_VISUAL[backendHealth.status].dot].join(' ')} />
              <span className="text-gray-400">{HEALTH_VISUAL[backendHealth.status].tip}</span>
            </span>
          )}
          {messages.length > 0 && !isRunning && (
            <button
              onClick={clearMessages}
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
      </div>

      {/* ── 工具调用时间线面板（审计链路，Phase 1）── */}
      {sessionId && <PipelineTimeline sessionId={sessionId} />}

      {/* ── TODO 规划卡片 ── */}
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
            setActiveRole(role.id, role.name, role.icon, role.color)
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
        {/* 空状态引导（快捷提示词跟随角色） */}
        {messages.length === 0 && !isRunning && (
          <div className="flex flex-col items-center justify-center h-full text-center py-8 space-y-3">
            <div className="w-14 h-14 bg-gradient-to-br from-orange-50 to-amber-50 rounded-2xl flex items-center justify-center shadow-sm">
              <span className="text-2xl">{activeRoleIcon}</span>
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-gray-700">告诉 {activeRoleName} 你想做什么</p>
              <p className="text-xs text-gray-400 max-w-[200px] leading-relaxed">
                直接说话，或粘贴 Sky JSON / 音频文件
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5 justify-center max-w-[240px]">
              {roleHints.map((hint) => (
                <button
                  key={hint}
                  onClick={() => insertTextRef.current?.(hint)}
                  className="text-xs px-2.5 py-1 bg-gray-50 hover:bg-orange-50 hover:text-orange-500 text-gray-500 rounded-lg transition-colors border border-gray-100 hover:border-orange-200"
                >
                  {hint}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-gray-300 flex items-center gap-1">
              <span>💡</span>
              <span>支持粘贴 Sky JSON / MP3 / MIDI 文件</span>
            </p>
          </div>
        )}

        <ChatMessageList messages={messages} />

        {isRunning && hasStreamContent && (
          <StreamingAssistantCard
            content={streaming.content}
            reasoningContent={streaming.reasoning_content}
            toolCalls={streaming.tool_calls}
            roundIdx={streaming.roundIdx}
          />
        )}

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
          <button onClick={resetRuntime} className="shrink-0 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      {/* ── 输入区 ── */}
      <InputBox
        isRunning={isRunning}
        sessionId={sessionId}
        placeholder={placeholder}
        ctxPct={ctxPct}
        activeRoleIcon={activeRoleIcon}
        activeRoleName={activeRoleName}
        onOpenRolePanel={() => setShowRolePanel(true)}
        attachment={attachment}
        onClearAttachment={clearAttachment}
        uploadTip={uploadTip}
        models={models}
        activeModelId={activeModelId}
        showModelMenu={showModelMenu}
        onToggleModelMenu={() => setShowModelMenu((v) => !v)}
        onCloseModelMenu={() => setShowModelMenu(false)}
        onSelectModel={(id) => void handleSelectModel(id)}
        onSend={handleRichSend}
        onAbort={handleAbort}
        onClearMessages={clearMessages}
        insertTextRef={insertTextRef}
        richSendRef={richSendRef}
        insertMentionRef={insertMentionRef}
        onPaste={handlePaste}
        onImageUploadStatus={(s, name) => {
          // uploadTip 由 useAttachment 内部管理，此处仅转发图片上传状态
          if (name) console.debug('[img upload]', s, name)
        }}
      />

      {/* ── 删除对话确认弹窗 ── */}
      {deleteConfirmId && (() => {
        const sess = allSessions.find((s) => s.id === deleteConfirmId)
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
            onConfirm={handleConfirmDelete}
            onCancel={() => setDeleteConfirmId(null)}
          />
        )
      })()}
    </div>
  )
}
