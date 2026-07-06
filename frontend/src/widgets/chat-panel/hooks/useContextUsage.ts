'use client'

/**
 * useContextUsage — 上下文用量 hook（事件驱动，无轮询）
 *
 * 优化点：
 *   - 不再定期轮询，改为事件驱动：session 切换 或 消息数变化后才查一次
 *   - 运行中（status=running）不查询（后端正忙，值无意义）
 *   - 降级策略：后端不可用时用本地消息字符数估算
 *   - 与 ChatPanel 解耦，可独立测试
 */

import { useState, useEffect, useRef } from 'react'
import { getContextUsage } from '@/shared/lib/api'
import type { ChatMessage } from '@/features/chat/types/chat.types'

interface UseContextUsageOptions {
  sessionId: string | null
  status: string
  messages: ChatMessage[]
}

export function useContextUsage({ sessionId, status, messages }: UseContextUsageOptions) {
  const [ctxPct, setCtxPct] = useState(0)
  // 上次查询时的消息数，用于判断是否需要重新查询
  const lastMsgCountRef = useRef(-1)
  const lastSessionRef  = useRef<string | null>(null)

  useEffect(() => {
    // 运行中不查（后端正忙，值无意义）
    if (!sessionId || status === 'running') return

    const msgCount = messages.length
    const sessionChanged = lastSessionRef.current !== sessionId
    // 修复：session 切换后 messages 被清空为 0，若切换前后 length 相同则 msgsChanged=false
    // 导致新 session 首次不触发查询。加入 sessionChanged 强制触发，保证切换后必查一次。
    const msgsChanged = lastMsgCountRef.current !== msgCount || sessionChanged

    // session 未变且消息数未变：跳过，避免重复请求
    if (!sessionChanged && !msgsChanged) return

    lastSessionRef.current  = sessionId
    lastMsgCountRef.current = msgCount

    let cancelled = false

    getContextUsage(sessionId)
      .then(({ pct }) => { if (!cancelled) setCtxPct(pct) })
      .catch(() => {
        // 降级：用本地消息字符数估算
        if (!cancelled) {
          const chars = messages.reduce((acc, m) => acc + (m.content?.length ?? 0), 0)
          setCtxPct(Math.min(99, Math.round(chars / 5120)))
        }
      })

    return () => { cancelled = true }
  }, [sessionId, status, messages.length])

  return ctxPct
}
