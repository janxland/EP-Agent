/**
 * chat.store.ts — 对话状态管理
 *
 * 设计原则：
 *   - 单一事实来源：messages 数组 = 已落库消息，streaming = 临时流式状态
 *   - 严格区分：streaming 消息不进 messages，commit 后才进
 *   - O(1) 工具匹配：tool message 通过 tool_call_id 精确对应 assistant tool_calls
 *   - SSE 驱动：所有状态变更由后端 SSE 事件触发，前端不猜测完成时机
 */

import { create } from 'zustand'
import type {
  ChatMessage,
  ChatUserMessage,
  ChatAssistantMessage,
  ChatToolMessage,
  ChatRunStatus,
  ToolCall,
} from '../types/chat.types'
import type { SSEEvent, ToolCallPayload, PipelineStepPayload } from '@/shared/types'

// ─── 流式累积状态（临时，不进 messages）──────────────────────────────────────

export interface StreamState {
  content: string
  reasoning_content: string
  /** 累积中的工具调用（arguments 在流式过程中逐步拼接，可能不完整） */
  tool_calls: ToolCall[]
  /** 当前 ReAct 轮次（从 0 开始，多轮时前端展示「第 N 轮」） */
  roundIdx: number
  /** 当前流式回合 ID（后端每轮 ReAct 生成，用于隔离多轮输出） */
  streamTurnId: string | null
}

const emptyStream = (): StreamState => ({
  content: '',
  reasoning_content: '',
  tool_calls: [],
  roundIdx: 0,
  streamTurnId: null,
})

// ─── Store 类型 ───────────────────────────────────────────────────────────────

// ─── TODO 项类型 ────────────────────────────────────────────────────────────

export interface TodoItem {
  id: string
  title: string
  detail: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  /** 衍生子任务时，记录父任务 id（顶层任务无此字段） */
  parent_id?: string
}

interface ChatStoreState {
  /** 已落库的对话消息（user / assistant / tool） */
  messages: ChatMessage[]
  /** 流式临时状态（未 commit，不在 messages 中） */
  streaming: StreamState
  /** 运行状态 */
  status: ChatRunStatus
  /** 当前步骤文字（pipeline.step 事件） */
  currentStep: string | null
  /** 错误信息 */
  errorMessage: string | null
  /** 当前轮次的 TODO 列表 */
  todos: TodoItem[]
  /** TODO 摘要 */
  todoSummary: string
  /** 当前意图域（来自 todo.list 事件的 domain 字段） */
  todoDomain: string
  /** 当前激活的角色 ID（来自 role.active SSE 事件） */
  activeRoleId: string
  /** 当前激活的角色名称（前端展示用） */
  activeRoleName: string
  /** 当前激活的角色图标 */
  activeRoleIcon: string
  /** 当前激活的角色主题色 */
  activeRoleColor: string
  /** 角色是否已从服务端恢复（防止重复 fetch） */
  _roleRestored: boolean

  // ── Actions ──
  /** 从 /api/sessions/{id}/role 恢复角色状态（刷新后调用） */
  restoreRoleFromSession: (sessionId: string) => Promise<void>
  /** 直接设置角色（切换后立即更新） */
  setActiveRole: (roleId: string, roleName: string, icon: string, color: string) => void
  setMessages: (messages: ChatMessage[]) => void
  addMessage: (message: ChatMessage) => void
  /** 乐观插入用户消息（发送时立即显示） */
  addOptimisticUserMessage: (content: string) => ChatUserMessage
  /** 追加文本增量 */
  appendStreamingChunk: (chunk: string) => void
  /** 追加推理增量 */
  appendReasoningChunk: (chunk: string) => void
  /** 追加/合并工具调用增量（arguments 流式拼接） */
  appendToolCallChunk: (toolCall: ToolCall) => void
  /** 将 streaming 状态提交为正式 assistant 消息 */
  commitStreamMessage: () => void
  /** 开始一次 run（清空 streaming，重置状态） */
  startRun: () => void
  /** 正常结束（由 SSE message.completed 触发） */
  finishRun: () => void
  /** 异常结束 */
  failRun: (error: string) => void
  /** 重置运行时状态（不清空 messages） */
  resetRuntime: () => void
  setCurrentStep: (step: string | null) => void
  setTodos: (todos: TodoItem[], summary?: string, domain?: string) => void

  /** 角色切换欢迎语直接注入对话框（修复：原来用 window.dispatchEvent 但无监听者） */
  addGreetingMessage: (greeting: string, roleName: string, roleIcon: string) => void

  // ── SSE 统一入口（对接 EP-Agent 后端 SSE 格式）──
  handleSSEEvent: (event: SSEEvent) => void
}

// ─── ID 生成 ──────────────────────────────────────────────────────────────────

let _msgCounter = 0
const newMsgId = () => `msg_${++_msgCounter}_${Date.now()}`

// ─── Store 实现 ───────────────────────────────────────────────────────────────

export const useChatStore = create<ChatStoreState>((set, get) => ({
  messages: [],
  streaming: emptyStream(),
  status: 'idle',
  currentStep: null,
  errorMessage: null,
  todos: [],
  todoSummary: '',
  todoDomain: '',
  activeRoleId: 'abc_expert',
  activeRoleName: 'Sky 乐谱专家',
  activeRoleIcon: '🎵',
  activeRoleColor: 'orange',
  _roleRestored: false,

  setMessages: (messages) => {
    // 合并去重：以 incoming（HTTP 历史）为基础，追加 store 中 SSE 已注入但 HTTP 历史里没有的消息
    // 防止 HTTP 拉取与 SSE replay 竞态互相覆盖
    const incoming = messages
    set((s) => {
      if (s.messages.length === 0) {
        // store 为空时直接设置（最常见路径，避免多余遍历）
        return { messages: incoming }
      }
      // incomingIds：HTTP 历史里已有的 id 集合
      const incomingIds = new Set(incoming.map((m) => m.id))
      // extra：store 中 HTTP 历史未包含的消息（SSE replay 已注入的新消息）
      const extra = s.messages.filter((m) => !incomingIds.has(m.id))
      if (extra.length === 0) return { messages: incoming }
      // 合并：incoming（HTTP 历史，有序）+ extra（SSE 新增），按 createdAt 排序
      const merged = [...incoming, ...extra].sort((a, b) =>
        (a.createdAt ?? '').localeCompare(b.createdAt ?? '')
      )
      return { messages: merged }
    })
  },

  addMessage: (message) =>
    set((s) => ({ messages: [...s.messages, message] })),

  addGreetingMessage: (greeting, roleName, roleIcon) => {
    const msg: ChatAssistantMessage = {
      id: newMsgId(),
      role: 'assistant',
      content: `${roleIcon} **${roleName}** 已就绪\n\n${greeting}`,
      createdAt: new Date().toISOString(),
      kind: 'turn',
    }
    set((s) => ({ messages: [...s.messages, msg] }))
  },

  addOptimisticUserMessage: (content) => {
    const msg: ChatUserMessage = {
      id: newMsgId(),
      role: 'user',
      content,
      createdAt: new Date().toISOString(),
    }
    set((s) => ({ messages: [...s.messages, msg] }))
    return msg
  },

  appendStreamingChunk: (chunk) =>
    set((s) => ({
      streaming: { ...s.streaming, content: s.streaming.content + chunk },
    })),

  appendReasoningChunk: (chunk) =>
    set((s) => ({
      streaming: {
        ...s.streaming,
        reasoning_content: s.streaming.reasoning_content + chunk,
      },
    })),

  appendToolCallChunk: (toolCall) =>
    set((s) => {
      const existing = s.streaming.tool_calls.find((t) => t.id === toolCall.id)
      if (existing) {
        // 同一 call_id：拼接 arguments（流式分片）
        return {
          streaming: {
            ...s.streaming,
            tool_calls: s.streaming.tool_calls.map((t) =>
              t.id === toolCall.id
                ? {
                    ...t,
                    function: {
                      ...t.function,
                      arguments: t.function.arguments + toolCall.function.arguments,
                    },
                  }
                : t
            ),
          },
        }
      }
      // 新工具调用：追加
      return {
        streaming: {
          ...s.streaming,
          tool_calls: [...s.streaming.tool_calls, toolCall],
        },
      }
    }),

  commitStreamMessage: () => {
    set((s) => {
      const { streaming } = s
      const hasContent = streaming.content.trim().length > 0
      const hasTools = streaming.tool_calls.length > 0
      const hasReasoning = streaming.reasoning_content.trim().length > 0
      if (!hasContent && !hasTools && !hasReasoning) return {}
      const msg: ChatAssistantMessage = {
        id: newMsgId(),
        role: 'assistant',
        content: streaming.content,
        reasoning_content: hasReasoning ? streaming.reasoning_content : undefined,
        tool_calls: hasTools ? streaming.tool_calls : undefined,
        createdAt: new Date().toISOString(),
        kind: 'turn',
      }
      return {
        messages: [...s.messages, msg],
        streaming: emptyStream(),
      }
    })
  },

  startRun: () =>
    set({
      status: 'running',
      errorMessage: null,
      currentStep: null,
      streaming: emptyStream(),
      // TODO 不立即清空：等 todo.list 事件覆盖，避免发消息瞬间卡片闪烁
      // activeRole* 跨轮次保持，_roleRestored 不重置
    }),

  finishRun: () => {
    // tool.call(succeeded) 已 commit 过；此处仅处理纯文本轮次（无工具调用）的收尾
    set((s) => {
      const { streaming } = s
      const hasContent = streaming.content.trim().length > 0
      const hasTools   = streaming.tool_calls.length > 0
      const hasReason  = streaming.reasoning_content.trim().length > 0
      if (!hasContent && !hasTools && !hasReason) {
        return { status: 'completed' as const, currentStep: null }
      }
      const msg: ChatAssistantMessage = {
        id: newMsgId(),
        role: 'assistant',
        content: streaming.content,
        reasoning_content: hasReason ? streaming.reasoning_content : undefined,
        tool_calls: hasTools ? streaming.tool_calls : undefined,
        createdAt: new Date().toISOString(),
        kind: 'turn',
      }
      return {
        messages: [...s.messages, msg],
        streaming: emptyStream(),
        status: 'completed' as const,
        currentStep: null,
      }
    })
  },

  failRun: (error) => {
    set((s) => {
      const { streaming } = s
      const hasContent = streaming.content.trim().length > 0
      const hasTools   = streaming.tool_calls.length > 0
      const hasReason  = streaming.reasoning_content.trim().length > 0
      if (!hasContent && !hasTools && !hasReason) {
        return { status: 'failed' as const, errorMessage: error, currentStep: null }
      }
      const msg: ChatAssistantMessage = {
        id: newMsgId(),
        role: 'assistant',
        content: streaming.content,
        reasoning_content: hasReason ? streaming.reasoning_content : undefined,
        tool_calls: hasTools ? streaming.tool_calls : undefined,
        createdAt: new Date().toISOString(),
        kind: 'turn',
      }
      return {
        messages: [...s.messages, msg],
        streaming: emptyStream(),
        status: 'failed' as const,
        errorMessage: error,
        currentStep: null,
      }
    })
  },

  resetRuntime: () =>
    set({
      status: 'idle',
      errorMessage: null,
      currentStep: null,
      streaming: emptyStream(),
      // session 切换：清空 TODO 避免旧卡片残留，重置角色恢复标记
      todos: [],
      todoSummary: '',
      todoDomain: '',
      _roleRestored: false,
    }),

  restoreRoleFromSession: async (sessionId) => {
    // 已恢复过就不重复 fetch
    if (get()._roleRestored) return
    try {
      const res = await fetch(`/api/sessions/${sessionId}/role`)
      if (!res.ok) return
      const data = await res.json()
      if (data.role_id) {
        set({
          activeRoleId:    data.role_id,
          activeRoleName:  data.role_name  ?? 'Sky 乐谱专家',
          activeRoleIcon:  data.icon       ?? '🎵',
          activeRoleColor: data.color      ?? 'orange',
          _roleRestored:   true,
        })
      }
    } catch {
      // 静默失败，保持默认角色
    }
  },

  setActiveRole: (roleId, roleName, icon, color) =>
    set({
      activeRoleId:    roleId,
      activeRoleName:  roleName,
      activeRoleIcon:  icon,
      activeRoleColor: color,
      _roleRestored:   true,
    }),

  setCurrentStep: (step) => set({ currentStep: step }),

  setTodos: (todos, summary, domain) => set({ todos, todoSummary: summary ?? '', todoDomain: domain ?? '' }),

  // ── SSE 事件统一处理 ───────────────────────────────────────────────────────
  //
  // 事件流时序（正常情况）：
  //   pipeline.step(running)
  //   → message.delta × N        （文本流式输出）
  //   → tool.call(running) × M   （工具调用开始，追加到 streaming.tool_calls）
  //   → tool.call(succeeded) × M （工具结果，commit assistant → 追加 tool message）
  //   → message.completed        （整轮结束，commit 剩余 streaming）
  //   → abc.updated              （可选，谱子更新）
  //
  handleSSEEvent: (event: SSEEvent) => {
    const { setCurrentStep, appendStreamingChunk, appendReasoningChunk,
            appendToolCallChunk, commitStreamMessage, addMessage, failRun, finishRun } = get()

    switch (event.type) {

      // ── TODO 列表（Agent 意图规划）────────────────────────────────────────
      case 'todo.list': {
        const p = event.payload as { todos: TodoItem[]; summary?: string; domain?: string }
        set({ todos: p.todos ?? [], todoSummary: p.summary ?? '', todoDomain: p.domain ?? '' })
        break
      }

      case 'todo.update': {
        const p = event.payload as { id: string; status: TodoItem['status'] }
        set((s) => ({
          todos: s.todos.map((t) => (t.id === p.id ? { ...t, status: p.status } : t)),
        }))
        break
      }

      // ── 动态子任务衍生（todo.append）─────────────────────────────────────
      // 后端在执行某步骤时发现需要拆分，追加子任务到前端列表。
      // 不覆盖现有 todos，只追加新子任务（带 parent_id 用于缩进展示）。
      case 'todo.append': {
        const p = event.payload as { parent_id: string; todos: TodoItem[] }
        const newTodos = (p.todos ?? []).map((t) => ({ ...t, parent_id: p.parent_id }))
        set((s) => ({
          todos: [
            ...s.todos,
            ...newTodos.filter((n) => !s.todos.some((t) => t.id === n.id)),
          ],
        }))
        break
      }

      // ── 步骤进度 ──────────────────────────────────────────────────────────
      case 'pipeline.step': {
        const p = event.payload as PipelineStepPayload & { round_idx?: number; stream_turn_id?: string }
        setCurrentStep(p.text)
        // 步骤开始时标记 running（如果还没开始）
        if (p.status === 'running') {
          set((s) => s.status === 'idle' ? { status: 'running' } : {})
        }
        // 新 ReAct 轮次开始：先 commit 上一轮 streaming，再 reset 为新轮次
        // 这是解决多轮 ReAct 流式输出混乱的关键：每轮用独立 stream_turn_id 隔离
        if (typeof p.round_idx === 'number' && p.stream_turn_id) {
          const newTurnId = p.stream_turn_id
          set((s) => {
            // 若 turn_id 相同（重复事件）则跳过
            if (s.streaming.streamTurnId === newTurnId) return {}
            // 先检查上一轮是否有未 commit 的内容
            const prev = s.streaming
            const prevHasContent = prev.content.trim().length > 0
            const prevHasTools   = prev.tool_calls.length > 0
            const prevHasReason  = prev.reasoning_content.trim().length > 0
            if (prevHasContent || prevHasTools || prevHasReason) {
              // 上一轮有内容：commit 为正式消息
              const msg: ChatAssistantMessage = {
                id: newMsgId(),
                role: 'assistant',
                content: prev.content,
                reasoning_content: prevHasReason ? prev.reasoning_content : undefined,
                tool_calls: prevHasTools ? prev.tool_calls : undefined,
                createdAt: new Date().toISOString(),
                kind: 'turn',
              }
              return {
                messages: [...s.messages, msg],
                streaming: { ...emptyStream(), roundIdx: p.round_idx as number, streamTurnId: newTurnId },
              }
            }
            // 上一轮无内容：直接 reset
            return {
              streaming: { ...emptyStream(), roundIdx: p.round_idx as number, streamTurnId: newTurnId },
            }
          })
        } else if (typeof p.round_idx === 'number') {
          // 兼容无 stream_turn_id 的旧事件：仅更新轮次
          set((s) => ({
            streaming: { ...s.streaming, roundIdx: p.round_idx as number }
          }))
        }
        break
      }

      // ── 文本流式增量 ──────────────────────────────────────────────────────
      case 'message.delta': {
        const p = event.payload as { delta?: string; reasoning_delta?: string }
        if (p.delta) appendStreamingChunk(p.delta)
        if (p.reasoning_delta) appendReasoningChunk(p.reasoning_delta)
        set((s) => s.status !== 'running' ? { status: 'running' } : {})
        break
      }

      // ── 消息完成（整轮结束信号）──────────────────────────────────────────
      // finishRun 内部已处理：有 streaming 内容则 commit，无则仅更新状态
      case 'message.completed': {
        finishRun()
        break
      }

      // ── 历史消息回放（刷新后 SSE replay，不触发 running 状态）────────────
      // 后端 SSE 连接建立时推送 message.history 事件，前端直接追加到 messages
      // 不经过 streaming 流程，不影响当前 status，实现无感知历史恢复。
      case 'message.history': {
        const p = event.payload as {
          role: string
          content: string
          msg_id?: string
          tool_call_id?: string
          name?: string
          // assistant 消息携带 tool_calls（刷新后工具卡片渲染的关键）
          tool_calls?: ToolCall[]
        }
        // user / tool 消息：空内容直接忽略，避免空消息气泡
        // assistant 消息：可能 content="" 但有 tool_calls（纯工具调用轮次），不能跳过
        if (p.role !== 'assistant' && !p.content?.trim()) break
        if (p.role === 'assistant' && !p.content?.trim() && !p.tool_calls?.length) break

        const existing = get().messages
        // 生成稳定 ID：优先使用后端 msg_id；为空时基于内容 hash 生成，保证重连幂等
        const msgId = (p.msg_id && p.msg_id.length > 0)
          ? p.msg_id
          : `hist_${p.role}_${p.content.slice(0, 32).replace(/\s+/g, '_')}`
        // 去重：已存在相同 id 的消息直接跳过
        if (existing.some((m) => m.id === msgId)) break

        if (p.role === 'user') {
          const userMsg: ChatUserMessage = {
            id: msgId,
            role: 'user',
            content: p.content,
            createdAt: event.timestamp,
          }
          set((s) => ({ messages: [...s.messages, userMsg] }))
        } else if (p.role === 'assistant') {
          const assistantMsg: ChatAssistantMessage = {
            id: msgId,
            role: 'assistant',
            content: p.content,
            // 还原 tool_calls：前端渲染工具卡片时通过 tool_call_id 匹配 tool message
            tool_calls: p.tool_calls?.length ? p.tool_calls : undefined,
            createdAt: event.timestamp,
            kind: 'turn',
          }
          set((s) => ({ messages: [...s.messages, assistantMsg] }))
        } else if (p.role === 'tool') {
          // ── 工具结果体恢复（刷新后 SSE replay 推送 tool message）──────────
          // tool_call_id 用于与 assistant tool_calls 匹配，渲染工具结果卡片
          const toolMsg: ChatToolMessage = {
            id: msgId,
            role: 'tool',
            tool_call_id: p.tool_call_id ?? '',
            name: p.name,
            content: p.content,
            createdAt: event.timestamp,
          }
          set((s) => ({ messages: [...s.messages, toolMsg] }))
        }
        break
      }

      // ── 工具调用 ──────────────────────────────────────────────────────────
      // tool.call(running)   → 追加到 streaming.tool_calls（卡片显示进行中）
      // tool.call(succeeded) → commit 当前 streaming assistant → 追加 tool message
      // tool.call(failed)    → 同上，内容为错误信息
      case 'tool.call': {
        const p = event.payload as ToolCallPayload

        if (p.status === 'running') {
          // 工具开始：把工具调用追加到 streaming（前端实时展示卡片）
          appendToolCallChunk({
            id: p.call_id,
            type: 'function',
            function: {
              name: p.tool,
              arguments: JSON.stringify(p.arguments ?? {}),
            },
          })
          set((s) => s.status !== 'running' ? { status: 'running' } : {})

        } else if (p.status === 'succeeded' || p.status === 'failed') {
          // 工具完成：
          //   1. 先 commit streaming（把包含该工具的 assistant 消息落库）
          //   2. 再追加 tool message（通过 tool_call_id O(1) 匹配）
          //   3. 若是文件操作工具（写/删/重命名/上传），触发文件树刷新
          commitStreamMessage()

          const toolMsg: ChatToolMessage = {
            id: newMsgId(),
            role: 'tool',
            tool_call_id: p.call_id,
            name: p.tool,
            content: p.status === 'failed'
              ? `失败: ${p.error ?? '未知错误'}`
              : (p.result_preview ?? ''),
            createdAt: new Date().toISOString(),
          }
          addMessage(toolMsg)

          // 文件操作工具成功完成时刷新文件树
          if (p.status === 'succeeded' && typeof window !== 'undefined') {
            const FILE_OP_TOOLS = new Set([
              // 旧工具名（兼容）
              'write_workspace_file', 'delete_workspace_file',
              'rename_workspace_file', 'copy_workspace_file',
              'upload_workspace_file', 'save_abc_score',
              'save_h5_file', 'create_workspace_dir',
              // 新工具名（去 ID 化后）
              'write_file', 'append_file', 'delete_file',
              'copy_file', 'rename_file', 'move_file',
              'abc_to_midi', 'generate_h5_from_midi',
              'generate_h5_from_abc', 'sovits_save_audio',
              'save_score_to_workspace',
            ])
            if (FILE_OP_TOOLS.has(p.tool)) {
              window.dispatchEvent(new CustomEvent('ep:workspace-refresh'))
            }
          }
        }
        break
      }

      // ── pipeline.state（后端 session 运行状态同步）────────────────────────
      // SSE 连接建立时 replay 推送，或任务完成/失败时推送。
      // 主要用途：刷新后解除前端 loading（running → idle），防止永久 loading。
      // chat.store 只处理 idle/completed 的收尾，running 状态由 startRun 管理。
      case 'pipeline.state': {
        const p = event.payload as { state?: string; _replay?: boolean }
        // 仅处理 replay 场景：后端推送 idle/completed 解除 loading
        // 实时场景（非 replay）由 message.completed / error 驱动，不重复处理
        if (p._replay) {
          const s = p.state ?? 'idle'
          if (s === 'idle' || s === 'completed') {
            // 若前端仍处于 running（刷新前任务未完成），强制重置为 idle
            set((cur) => cur.status === 'running' ? { status: 'idle', currentStep: null } : {})
          }
        }
        break
      }

      // ── abc.updated（谱子更新，scoreStore 处理，chatStore 不干预）────────
      // 谱子更新事件由 scoreStore（entities/session/store.ts）处理 ABC 和 meta 同步。
      // chatStore 不 commit/complete，避免提前截断流式输出。
      // 流程结束信号统一来自 message.completed。
      case 'abc.updated': {
        // chatStore 不处理此事件，仅 scoreStore 处理
        break
      }

      // ── 角色激活（每轮对话开始时后端推送当前角色）──────────────────────────
      // universal_runner 在路由完成后推送 role.active 事件，
      // 前端顶栏据此实时展示当前激活的专家角色。
      case 'role.active': {
        const p = event.payload as {
          role_id: string; role_name: string; icon: string; color: string
        }
        set({
          activeRoleId:    p.role_id   ?? 'abc_expert',
          activeRoleName:  p.role_name ?? 'Sky 乐谱专家',
          activeRoleIcon:  p.icon      ?? '🎵',
          activeRoleColor: p.color     ?? 'orange',
        })
        break
      }

      // ── H5 海报生成完成 ───────────────────────────────────────────────────
      // H5Agent 生成并保存 HTML 文件后推送此事件。
      // chat.store 将结果以 assistant 消息形式呈现（含可点击链接）。
      // 前端可据此展示"查看/下载 H5"按钮。
      case 'h5.ready': {
        const p = event.payload as {
          title?: string
          url_path?: string
          file_path?: string
          size_kb?: number
          template?: string
        }
        const title    = p.title    ?? 'H5 乐谱海报'
        const urlPath  = p.url_path ?? ''
        const sizeKb   = p.size_kb  ? `${p.size_kb} KB` : ''
        const template = p.template ?? 'apple'
        // H5 文件由后端静态服务托管，需拼接后端地址（绕过 Next.js 路由）
        const backendBase = process.env.NEXT_PUBLIC_BACKEND_URL ?? 'http://localhost:8080'
        const fullUrl = urlPath ? `${backendBase}${urlPath}` : ''
        const content  = [
          `🎨 **${title}** 已生成`,
          fullUrl ? `📎 [点击预览 H5 海报](${fullUrl})` : '',
          sizeKb  ? `📦 文件大小：${sizeKb}` : '',
          `🖼️ 模板：${template}`,
        ].filter(Boolean).join('\n')

        const h5Msg: ChatAssistantMessage = {
          id: newMsgId(),
          role: 'assistant',
          content,
          createdAt: new Date().toISOString(),
          kind: 'turn',
        }
        set((s) => ({ messages: [...s.messages, h5Msg] }))
        break
      }

      // ── 工作区文件已保存（谱子写入 .sky/ 后通知前端刷新文件树）────────────
      case 'workspace.file_saved':
      case 'workspace.scores':
      // ── 工具写入文件后的 SSE 事件（react_executor 推送）─────────────────────
      // display=false，不显示在聊天气泡中，仅用于触发文件树刷新
      case 'workspace.files.changed': {
        // 触发文件树刷新（fileTreeRefreshToken 递增，WorkspaceFileTree 监听此值）
        // 使用 window 事件总线避免循环依赖（workspace.store → chat.store 已有依赖链）
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('ep:workspace-refresh'))
        }
        break
      }

      // ── SSE 重连通知（断线后首次重连，清空消息等待 replay 重新填充）────────
      // api.ts 在 onerror retryCount===0 时发出此事件。
      // 清空 messages + todos，让后端 replay 重新推送历史，避免重复累积。
      case 'connection.reconnecting': {
        set({ messages: [], todos: [], todoSummary: '', todoDomain: '' })
        break
      }

      // ── 错误 ──────────────────────────────────────────────────────────────
      case 'error': {
        const msg = (event.payload as { message?: string }).message ?? '未知错误'
        failRun(msg)
        break
      }

      // 其他事件（connected、activity.update 等）忽略
      // checkSSEAlignment 在开发模式下警告未处理的事件类型
      default: {
        import('@/shared/lib/sse-alignment').then(({ checkSSEAlignment }) => {
          checkSSEAlignment(event.type)
        })
        break
      }
    }
  },
}))
