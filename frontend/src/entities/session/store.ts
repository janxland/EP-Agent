// Session & Score 全局状态管理
// 学习 magic-coding 的 features/chat/store/chat.store.ts 模式
// 使用 Zustand 管理本地 UI 状态

import { create } from 'zustand'
import type {
  Score,
  PipelineState,
  SSEEvent,
  PipelineStepPayload,
  ABCUpdatedPayload,
  MessageDeltaPayload,
  ToolCallPayload,
  IntentType,
} from '@/shared/types'

// ─── Pipeline Log（展示给用户的进度日志） ─────────────────────

export interface PipelineLog {
  id: string
  type: 'step' | 'activity' | 'message' | 'error' | 'tool_call'
  text: string
  status?: 'running' | 'succeeded' | 'failed'
  timestamp: Date
  // tool_call 专属
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolResult?: string
}

// ─── Store State ──────────────────────────────────────────────

interface ScoreState {
  // Session
  sessionId: string | null

  // Score（当前谱子）
  score: Score | null
  abcNotation: string | null    // 当前 ABC（可能比 score.abc_notation 更新）
  version: number

  // Pipeline 状态
  pipelineState: PipelineState
  pipelineLogs: PipelineLog[]
  streamingMessage: string      // 正在流式输出的消息

  // 最后一次编辑摘要
  lastEditSummary: string | null
  lastIntentType: IntentType | null

  // Actions
  setSessionId: (id: string) => void
  setScore: (score: Score) => void
  updateABC: (abc: string, version: number, summary?: string) => void
  setPipelineState: (state: PipelineState) => void
  appendLog: (log: Omit<PipelineLog, 'id' | 'timestamp'>) => void
  appendStreamDelta: (delta: string) => void
  commitStreamMessage: () => void
  clearLogs: () => void
  handleSSEEvent: (event: SSEEvent) => void
  reset: () => void
}

let logCounter = 0
function newLogId() {
  return `log_${++logCounter}`
}

export const useScoreStore = create<ScoreState>((set, get) => ({
  sessionId: null,
  score: null,
  abcNotation: null,
  version: 0,
  pipelineState: 'idle',
  pipelineLogs: [],
  streamingMessage: '',
  lastEditSummary: null,
  lastIntentType: null,

  setSessionId: (id) => set({ sessionId: id }),

  setScore: (score) =>
    set({
      score,
      abcNotation: score.abc_notation,
      version: score.version,
    }),

  updateABC: (abc, version, summary) =>
    set((s) => ({
      abcNotation: abc,
      version,
      lastEditSummary: summary ?? s.lastEditSummary,
      score: s.score ? { ...s.score, abc_notation: abc, version } : s.score,
    })),

  setPipelineState: (state) => set({ pipelineState: state }),

  appendLog: (log) =>
    set((s) => ({
      pipelineLogs: [
        ...s.pipelineLogs,
        { ...log, id: newLogId(), timestamp: new Date() },
      ],
    })),

  appendStreamDelta: (delta) =>
    set((s) => ({ streamingMessage: s.streamingMessage + delta })),

  commitStreamMessage: () =>
    set((s) => {
      if (!s.streamingMessage) return {}
      return {
        pipelineLogs: [
          ...s.pipelineLogs,
          {
            id: newLogId(),
            type: 'message' as const,
            text: s.streamingMessage,
            timestamp: new Date(),
          },
        ],
        streamingMessage: '',
      }
    }),

  clearLogs: () => set({ pipelineLogs: [], streamingMessage: '' }),

  // ── SSE 事件统一处理 ─────────────────────────────────────
  handleSSEEvent: (event: SSEEvent) => {
    const { appendLog, appendStreamDelta, commitStreamMessage, updateABC } = get()

    switch (event.type) {
      case 'pipeline.step': {
        const p = event.payload as PipelineStepPayload
        // pipelineState 跟随步骤状态变化
        const nextState: PipelineState =
          p.status === 'running' ? 'running'
          : p.status === 'failed'  ? 'failed'
          : 'succeeded'
        set({ pipelineState: nextState })
        // event.display === false 时跳过日志（后端标记的内部步骤）
        if (event.display !== false) {
          appendLog({ type: 'step', text: p.text, status: p.status })
        }
        break
      }

      case 'abc.updated': {
        const p = event.payload as ABCUpdatedPayload
        updateABC(p.abc, p.version, p.summary)
        if (p.summary) {
          appendLog({ type: 'activity', text: `✓ ${p.summary}` })
        }
        break
      }

      case 'activity.update': {
        const text = (event.payload as { text?: string }).text ?? ''
        appendLog({ type: 'activity', text })
        break
      }

      case 'tool.call': {
        const p = event.payload as ToolCallPayload
        const callId = p.call_id
        const toolName = p.tool ?? 'unknown'
        const isRunning = p.status === 'running'
        const isFailed = p.status === 'failed'
        const text = isRunning
          ? `调用工具 ${toolName}...`
          : isFailed
          ? `工具 ${toolName} 失败: ${p.error ?? ''}`
          : `工具 ${toolName} 完成`

        set((s) => {
          if (isRunning) {
            // running：以 call_id 为 log.id append 新条目，后续 O(1) 精确定位
            return {
              pipelineLogs: [
                ...s.pipelineLogs,
                {
                  id: callId,           // call_id 即 log id，唯一且稳定
                  type: 'tool_call' as const,
                  text,
                  status: 'running' as const,
                  toolName,
                  toolArgs: p.arguments,
                  timestamp: new Date(),
                },
              ],
            }
          }
          // succeeded / failed：按 call_id 精确 map，O(1)，无并发竞态
          return {
            pipelineLogs: s.pipelineLogs.map((l) =>
              l.id === callId
                ? { ...l, text, status: isFailed ? ('failed' as const) : ('succeeded' as const), toolResult: p.result_preview }
                : l
            ),
          }
        })
        break
      }

      case 'message.delta': {
        // 后端实现流式文本输出时使用，当前为预留分支
        const p = event.payload as MessageDeltaPayload
        appendStreamDelta(p.delta)
        break
      }

      case 'message.completed': {
        // 后端实现流式文本输出时使用，当前为预留分支
        commitStreamMessage()
        break
      }

      case 'error': {
        const p = event.payload as { message?: string }
        appendLog({ type: 'error', text: p.message ?? '未知错误', status: 'failed' })
        set({ pipelineState: 'failed' })
        break
      }
    }
  },

  reset: () =>
    set({
      score: null,
      abcNotation: null,
      version: 0,
      pipelineState: 'idle',
      pipelineLogs: [],
      streamingMessage: '',
      lastEditSummary: null,
      lastIntentType: null,
    }),
}))
