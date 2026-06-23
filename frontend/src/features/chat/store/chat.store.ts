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
}

const emptyStream = (): StreamState => ({
  content: '',
  reasoning_content: '',
  tool_calls: [],
})

// ─── Store 类型 ───────────────────────────────────────────────────────────────

interface ChatStoreState {
  /** 已落库的对话消息（user / assistant / tool） */
  messages: ChatMessage[]
  /** 流式临时状态（未 commit，不在 messages 中） */
  streaming: StreamState
  /** 当前流式回合 ID，用于去重 */
  activeStreamTurnId: string | null
  /** 运行状态 */
  status: ChatRunStatus
  /** 当前步骤文字（pipeline.step 事件） */
  currentStep: string | null
  /** 错误信息 */
  errorMessage: string | null

  // ── Actions ──
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
  /** 正常结束（由 SSE message.completed 或 abc.updated 触发） */
  finishRun: () => void
  /** 异常结束 */
  failRun: (error: string) => void
  /** 重置运行时状态（不清空 messages） */
  resetRuntime: () => void
  setCurrentStep: (step: string | null) => void

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
  activeStreamTurnId: null,
  status: 'idle',
  currentStep: null,
  errorMessage: null,

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((s) => ({ messages: [...s.messages, message] })),

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
    const { streaming, messages } = get()
    const hasContent = streaming.content.trim().length > 0
    const hasTools = streaming.tool_calls.length > 0
    const hasReasoning = streaming.reasoning_content.trim().length > 0
    if (!hasContent && !hasTools && !hasReasoning) return

    const msg: ChatAssistantMessage = {
      id: newMsgId(),
      role: 'assistant',
      content: streaming.content,
      reasoning_content: hasReasoning ? streaming.reasoning_content : undefined,
      tool_calls: hasTools ? streaming.tool_calls : undefined,
      createdAt: new Date().toISOString(),
      kind: 'turn',
    }
    set({
      messages: [...messages, msg],
      streaming: emptyStream(),
      activeStreamTurnId: null,
    })
  },

  startRun: () =>
    set({
      status: 'running',
      errorMessage: null,
      currentStep: null,
      streaming: emptyStream(),
      activeStreamTurnId: null,
    }),

  finishRun: () => {
    get().commitStreamMessage()
    set({ status: 'completed', currentStep: null })
  },

  failRun: (error) => {
    get().commitStreamMessage()
    set({ status: 'failed', errorMessage: error, currentStep: null })
  },

  resetRuntime: () =>
    set({
      status: 'idle',
      errorMessage: null,
      currentStep: null,
      streaming: emptyStream(),
      activeStreamTurnId: null,
    }),

  setCurrentStep: (step) => set({ currentStep: step }),

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
            appendToolCallChunk, commitStreamMessage, addMessage, failRun } = get()

    switch (event.type) {

      // ── 步骤进度 ──────────────────────────────────────────────────────────
      case 'pipeline.step': {
        const p = event.payload as PipelineStepPayload
        setCurrentStep(p.text)
        // 步骤开始时标记 running（如果还没开始）
        if (p.status === 'running') {
          set((s) => s.status === 'idle' ? { status: 'running' } : {})
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
      case 'message.completed': {
        commitStreamMessage()
        set({ status: 'completed', currentStep: null })
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
        }
        break
      }

      // ── abc.updated（谱子更新，同时视为本轮完成）─────────────────────────
      case 'abc.updated': {
        // 谱子更新说明 edit 流程完成，commit 并结束
        commitStreamMessage()
        set((s) => s.status === 'running'
          ? { status: 'completed', currentStep: null }
          : {}
        )
        break
      }

      // ── 错误 ──────────────────────────────────────────────────────────────
      case 'error': {
        const msg = (event.payload as { message?: string }).message ?? '未知错误'
        failRun(msg)
        break
      }

      // 其他事件（connected、activity.update 等）忽略
      default:
        break
    }
  },
}))
